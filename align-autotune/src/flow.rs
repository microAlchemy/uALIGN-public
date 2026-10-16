use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use crate::analysis::{evaluate_foms, AnalysisInputs, FoMReport};
use crate::config::AutoTuneConfig;
use crate::parsers::{
    parse_ngspice_log, parse_pex_spice_with_refs, parse_pnr_layout, parse_spice_value,
    SimMeasurements,
};
use crate::remediation::constraints::{parse_constraints_json, SoftConstraint};
use crate::remediation::diagnoser::{diagnose_and_remediate, DiagnosisContext};
use crate::runners::align::{stage_design_inputs, AlignRunner};
use crate::runners::magic::{extract_subckt_ports, MagicRunner};
use crate::runners::ngspice::{prepare_dut_testbench, NgspiceRunner};
use crate::runners::IterationWorkspace;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AutoTuneSummary {
    pub total_iterations: usize,
    pub converged: bool,
    pub final_foms: FoMReport,
}

impl fmt::Display for AutoTuneSummary {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "iterations={}, converged={}, {}",
            self.total_iterations, self.converged, self.final_foms
        )
    }
}

/// Runs the closed-loop ALIGN -> Magic PEX -> ngspice -> Remediation tuning flow.
pub fn run_autotune_loop(config: &AutoTuneConfig) -> Result<AutoTuneSummary> {
    if !config.netlist_path.is_file() || config.max_iterations == 0 {
        return Ok(AutoTuneSummary {
            total_iterations: 0,
            converged: false,
            final_foms: FoMReport::default(),
        });
    }

    let netlist_content = fs::read_to_string(&config.netlist_path)
        .with_context(|| format!("Failed to read netlist {}", config.netlist_path.display()))?;

    let mut current_constraints = load_initial_constraints(config)?;
    let reference_nets = collect_reference_nets(config, &current_constraints);
    let expected_ports = extract_subckt_ports(&netlist_content, &config.circuit);
    let mut diff_pairs = discover_diff_pairs(
        &netlist_content,
        &config.circuit,
        &current_constraints,
        &reference_nets,
    );
    let tb_path = discover_testbench(config);
    let magic_tech_dir = discover_magic_tech_dir(config);

    let align_runner = AlignRunner::default();
    let magic_runner = MagicRunner::default();
    let ngspice_runner = NgspiceRunner::default();

    // Run pre-layout baseline simulation once if a testbench is available
    let pre_sim = if let Some(ref tb) = tb_path {
        run_prelayout_baseline(config, &ngspice_runner, tb).ok()
    } else {
        None
    };

    let mut best_report = FoMReport::default();
    let mut best_constraints = current_constraints.clone();
    let mut total_iterations = 0;

    for iter in 0..config.max_iterations {
        total_iterations = iter + 1;
        let ws = IterationWorkspace::prepare(&config.work_dir, iter)?;

        let staged_netlist = stage_design_inputs(
            &config.circuit,
            &config.netlist_path,
            &current_constraints,
            &ws.design_dir,
        )?;
        let align_artifacts = align_runner.run(
            &config.circuit,
            &staged_netlist,
            &config.pdk_path,
            &ws.root_dir,
        )?;
        let geometry = parse_pnr_layout(&align_artifacts.pnr_json_path).ok();
        merge_diff_pairs_from_align_topology(&ws.root_dir, &config.circuit, &mut diff_pairs);

        let extracted = if let Some(ref tech_dir) = magic_tech_dir {
            magic_runner
                .run(
                    &config.circuit,
                    &align_artifacts.gds_path,
                    tech_dir,
                    &ws.root_dir,
                    &expected_ports,
                )
                .and_then(|spice_path| parse_pex_spice_with_refs(&spice_path, &reference_nets))
                .ok()
        } else {
            None
        };

        let post_sim = if let Some(ref tb) = tb_path {
            let extracted_spice = ws.root_dir.join("extracted.spice");
            if extracted_spice.is_file() {
                prepare_dut_testbench(tb, &extracted_spice, &ws.root_dir)
                    .and_then(|staged_tb| ngspice_runner.run(&staged_tb, &ws.root_dir))
                    .and_then(|log_path| parse_ngspice_log(&log_path))
                    .ok()
            } else {
                None
            }
        } else {
            None
        };

        let report = evaluate_foms(&AnalysisInputs {
            geometry: geometry.as_ref(),
            extracted: extracted.as_ref(),
            diff_pairs: &diff_pairs,
            pre_sim: pre_sim.as_ref(),
            post_sim: post_sim.as_ref(),
        });

        println!(
            "[iter {}] {} ({} constraints)",
            iter,
            report,
            current_constraints.len()
        );

        if iter == 0 || report.is_better_than(&best_report) {
            best_report = report.clone();
            best_constraints = current_constraints.clone();
        } else if best_report.is_better_than(&report) {
            // Roll back to the best-performing constraint set only when the new layout regressed
            current_constraints = best_constraints.clone();
        }

        if report.is_satisfactory() {
            save_best_constraints(config, &best_constraints)?;
            return Ok(AutoTuneSummary {
                total_iterations,
                converged: true,
                final_foms: report,
            });
        }

        if iter + 1 < config.max_iterations {
            let ctx = DiagnosisContext {
                report: &best_report,
                extracted: extracted.as_ref(),
                geometry: geometry.as_ref(),
                diff_pairs: &diff_pairs,
                reference_nets: &reference_nets,
                iteration: iter,
            };
            current_constraints = diagnose_and_remediate(&ctx, &current_constraints);
        }
    }

    save_best_constraints(config, &best_constraints)?;

    Ok(AutoTuneSummary {
        total_iterations,
        converged: best_report.is_satisfactory(),
        final_foms: best_report,
    })
}

fn save_best_constraints(
    config: &AutoTuneConfig,
    constraints: &[Box<dyn SoftConstraint>],
) -> Result<()> {
    if constraints.is_empty() {
        return Ok(());
    }
    fs::create_dir_all(&config.work_dir)?;
    let out_path = config
        .work_dir
        .join(format!("{}.const.json", config.circuit));
    let json_list: Vec<serde_json::Value> = constraints.iter().map(|c| c.to_json()).collect();
    fs::write(&out_path, serde_json::to_string_pretty(&json_list)?)?;
    Ok(())
}

fn load_initial_constraints(config: &AutoTuneConfig) -> Result<Vec<Box<dyn SoftConstraint>>> {
    let Some(parent) = config.netlist_path.parent() else {
        return Ok(Vec::new());
    };
    let const_path = parent.join(format!("{}.const.json", config.circuit));
    if !const_path.is_file() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&const_path)
        .with_context(|| format!("Failed to read {}", const_path.display()))?;
    parse_constraints_json(&content)
}

fn collect_reference_nets(
    config: &AutoTuneConfig,
    constraints: &[Box<dyn SoftConstraint>],
) -> Vec<String> {
    let mut refs = config.reference_nets.clone();
    for c in constraints {
        if matches!(c.constraint_name(), "PowerPorts" | "GroundPorts") {
            if let Some(ports) = c.to_json().get("ports").and_then(|v| v.as_array()) {
                for port in ports.iter().filter_map(|p| p.as_str()) {
                    if !refs.iter().any(|r| r.eq_ignore_ascii_case(port)) {
                        refs.push(port.to_string());
                    }
                }
            }
        }
    }
    refs
}

/// Discovers differential net pairs using ALIGN's topological `DP` subgraph rule
/// (matched transistor pairs sharing a common source/tail and bulk with distinct gates and drains)
/// plus any explicit `SymmetricNets` constraints.
pub fn discover_diff_pairs(
    netlist_content: &str,
    circuit: &str,
    constraints: &[Box<dyn SoftConstraint>],
    reference_nets: &[String],
) -> Vec<(String, String)> {
    let mut pairs: Vec<(String, String)> = Vec::new();

    for c in constraints {
        if c.constraint_name() == "SymmetricNets" {
            let v = c.to_json();
            if let (Some(n1), Some(n2)) = (
                v.get("net1").and_then(|x| x.as_str()),
                v.get("net2").and_then(|x| x.as_str()),
            ) {
                if !n1.eq_ignore_ascii_case(n2) {
                    add_unique_pair(&mut pairs, n1, n2);
                }
            }
        }
    }

    let mosfets = extract_subckt_mosfets(netlist_content, circuit);
    for i in 0..mosfets.len() {
        for j in (i + 1)..mosfets.len() {
            let m1 = &mosfets[i];
            let m2 = &mosfets[j];

            // Must match device model and sizing (W, L, NF, M)
            if !m1.model.eq_ignore_ascii_case(&m2.model) || !m1.sizing_matches(m2) {
                continue;
            }

            // Must share common non-reference source (tail) and bulk, with distinct non-diode gates and drains
            if !m1.source.eq_ignore_ascii_case(&m2.source)
                || !m1.bulk.eq_ignore_ascii_case(&m2.bulk)
                || is_ref_net(&m1.source, reference_nets)
            {
                continue;
            }
            if m1.gate.eq_ignore_ascii_case(&m2.gate) || m1.drain.eq_ignore_ascii_case(&m2.drain) {
                continue;
            }
            if m1.drain.eq_ignore_ascii_case(&m1.gate) || m2.drain.eq_ignore_ascii_case(&m2.gate) {
                continue;
            }
            if is_ref_net(&m1.gate, reference_nets) || is_ref_net(&m2.gate, reference_nets) {
                continue;
            }

            add_unique_pair(&mut pairs, &m1.gate, &m2.gate);
        }
    }

    pairs
}

/// Also merges differential pairs discovered by ALIGN's compiler in `1_topology/<circuit>.verilog.json`
/// (`DP_*` primitives with `GA`/`GB` pins and auto-generated `symmetric_nets`).
fn merge_diff_pairs_from_align_topology(
    work_dir: &Path,
    circuit: &str,
    pairs: &mut Vec<(String, String)>,
) {
    let topo_path = work_dir
        .join("1_topology")
        .join(format!("{}.verilog.json", circuit.to_ascii_lowercase()));
    let Ok(content) = fs::read_to_string(&topo_path) else {
        return;
    };
    let Ok(root) = serde_json::from_str::<serde_json::Value>(&content) else {
        return;
    };
    let Some(modules) = root.get("modules").and_then(|v| v.as_array()) else {
        return;
    };

    for module in modules {
        if let Some(instances) = module.get("instances").and_then(|v| v.as_array()) {
            for inst in instances {
                let template = inst
                    .get("abstract_template_name")
                    .or_else(|| inst.get("template_name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if !template.to_ascii_uppercase().starts_with("DP_") {
                    continue;
                }
                if let Some(fa_map) = inst.get("fa_map").and_then(|v| v.as_array()) {
                    let mut ga = None;
                    let mut gb = None;
                    for entry in fa_map {
                        let formal = entry.get("formal").and_then(|v| v.as_str()).unwrap_or("");
                        let actual = entry.get("actual").and_then(|v| v.as_str());
                        if formal.eq_ignore_ascii_case("GA") {
                            ga = actual;
                        } else if formal.eq_ignore_ascii_case("GB") {
                            gb = actual;
                        }
                    }
                    if let (Some(a), Some(b)) = (ga, gb) {
                        add_unique_pair(pairs, a, b);
                    }
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
struct MosfetInstance {
    drain: String,
    gate: String,
    source: String,
    bulk: String,
    model: String,
    w: Option<f64>,
    l: Option<f64>,
    nf: Option<f64>,
    m: Option<f64>,
}

impl MosfetInstance {
    fn sizing_matches(&self, other: &Self) -> bool {
        opt_f64_eq(self.w, other.w)
            && opt_f64_eq(self.l, other.l)
            && opt_f64_eq(self.nf, other.nf)
            && opt_f64_eq(self.m, other.m)
    }
}

fn opt_f64_eq(a: Option<f64>, b: Option<f64>) -> bool {
    match (a, b) {
        (Some(x), Some(y)) => {
            let scale = x.abs().max(y.abs()).max(1e-18);
            (x - y).abs() / scale < 1e-6
        }
        (None, None) => true,
        _ => false,
    }
}

fn extract_subckt_mosfets(netlist_content: &str, circuit: &str) -> Vec<MosfetInstance> {
    let mut in_target = false;
    let mut found_subckt = false;
    let mut mosfets = Vec::new();

    for raw_line in netlist_content.lines() {
        let line = raw_line.split(['$', ';']).next().unwrap_or("").trim();
        if line.is_empty() || line.starts_with('*') {
            continue;
        }
        let lower = line.to_ascii_lowercase();
        if lower.starts_with(".subckt ") {
            let toks: Vec<&str> = line.split_whitespace().collect();
            in_target = toks.len() >= 2 && toks[1].eq_ignore_ascii_case(circuit);
            if in_target {
                found_subckt = true;
            }
            continue;
        }
        if lower.starts_with(".ends") {
            in_target = false;
            continue;
        }
        if found_subckt && !in_target {
            continue;
        }

        if let Some(mos) = parse_mosfet_line(line) {
            mosfets.push(mos);
        }
    }

    mosfets
}

fn parse_mosfet_line(line: &str) -> Option<MosfetInstance> {
    let first = line.chars().next()?;
    if !first.eq_ignore_ascii_case(&'m') && !first.eq_ignore_ascii_case(&'x') {
        return None;
    }

    let tokens: Vec<&str> = line.split_whitespace().collect();
    if tokens.len() < 6 {
        return None;
    }

    // Standard 4-terminal MOSFET call: <inst> <d> <g> <s> <b> <model> [k=v ...]
    let model = tokens[5];
    if model.contains('=') {
        return None;
    }

    let mut w = None;
    let mut l = None;
    let mut nf = None;
    let mut m = None;

    for tok in &tokens[6..] {
        if let Some((k, v)) = tok.split_once('=') {
            let val = parse_spice_value(v);
            match k.to_ascii_lowercase().as_str() {
                "w" => w = val,
                "l" => l = val,
                "nf" => nf = val,
                "m" => m = val,
                _ => {}
            }
        }
    }

    Some(MosfetInstance {
        drain: tokens[1].to_string(),
        gate: tokens[2].to_string(),
        source: tokens[3].to_string(),
        bulk: tokens[4].to_string(),
        model: model.to_string(),
        w,
        l,
        nf,
        m,
    })
}

fn is_ref_net(net: &str, reference_nets: &[String]) -> bool {
    net.eq_ignore_ascii_case("0") || reference_nets.iter().any(|r| r.eq_ignore_ascii_case(net))
}

fn add_unique_pair(pairs: &mut Vec<(String, String)>, net_p: &str, net_n: &str) {
    let exists = pairs.iter().any(|(a, b)| {
        (a.eq_ignore_ascii_case(net_p) && b.eq_ignore_ascii_case(net_n))
            || (a.eq_ignore_ascii_case(net_n) && b.eq_ignore_ascii_case(net_p))
    });
    if !exists {
        pairs.push((net_p.to_string(), net_n.to_string()));
    }
}

/// Resolves the circuit testbench `tb.sp`:
/// 1. Explicit `config.testbench_path` (`--testbench`) if provided.
/// 2. Co-located `<circuit>_tb.sp` or `tb.sp` in the netlist's directory.
/// 3. `benchmarks/testbenches/<pdk_slug>/<circuit>/tb.sp` (deriving `<pdk_slug>` by stripping
///    `_mock_pdk`/`_pdk` from `pdk_path`, defaulting to `"sky130"`).
pub fn discover_testbench(config: &AutoTuneConfig) -> Option<PathBuf> {
    if let Some(ref explicit_tb) = config.testbench_path {
        if explicit_tb.is_file() {
            return Some(explicit_tb.clone());
        }
    }

    if let Some(parent) = config.netlist_path.parent() {
        for name in [format!("{}_tb.sp", config.circuit), "tb.sp".to_string()] {
            let local_tb = parent.join(name);
            if local_tb.is_file() {
                return Some(local_tb);
            }
        }
    }

    let pdk_slug = config
        .pdk_path
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| {
            let lower = s.to_ascii_lowercase();
            lower
                .strip_suffix("_mock_pdk")
                .or_else(|| lower.strip_suffix("_pdk"))
                .unwrap_or(&lower)
                .to_string()
        })
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "sky130".to_string());

    let rel_tb = Path::new("benchmarks/testbenches")
        .join(&pdk_slug)
        .join(&config.circuit)
        .join("tb.sp");

    for ancestor in config.pdk_path.ancestors() {
        let candidate = ancestor.join(&rel_tb);
        if candidate.is_file() {
            return Some(candidate);
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        for ancestor in cwd.ancestors() {
            let candidate = ancestor.join(&rel_tb);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    None
}

fn run_prelayout_baseline(
    config: &AutoTuneConfig,
    ngspice_runner: &NgspiceRunner,
    tb_path: &Path,
) -> Result<SimMeasurements> {
    let pre_dir = config.work_dir.join("pre_sim");
    let staged_tb = prepare_dut_testbench(tb_path, &config.netlist_path, &pre_dir)?;
    let log_path = ngspice_runner.run(&staged_tb, &pre_dir)?;
    parse_ngspice_log(&log_path)
}

fn discover_magic_tech_dir(config: &AutoTuneConfig) -> Option<PathBuf> {
    let pdk_name = config
        .pdk_path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("SKY130_PDK");
    let rel_tech = Path::new("benchmarks/magic_tech").join(pdk_name);

    for ancestor in config.pdk_path.ancestors() {
        let candidate = ancestor.join(&rel_tech);
        if candidate.is_dir() {
            return Some(candidate);
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for ancestor in cwd.ancestors() {
            let candidate = ancestor.join(&rel_tech);
            if candidate.is_dir() {
                return Some(candidate);
            }
        }
    }
    None
}
