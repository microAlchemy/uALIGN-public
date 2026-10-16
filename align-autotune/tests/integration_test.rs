use align_autotune::analysis::{
    evaluate_foms, rank_net_admittances, AnalysisInputs, FoM, FoMReport,
};
use align_autotune::config::{default_reference_nets, AutoTuneConfig};
use align_autotune::flow::{discover_diff_pairs, run_autotune_loop};
use align_autotune::parsers::{
    parse_ngspice_log_str, parse_pex_spice_str, parse_pex_spice_str_with_refs,
    parse_pnr_layout_str, ExtractedCircuit, LayoutGeometry, PexNet, SimMeasurements,
};
use align_autotune::remediation::constraints::{
    parse_constraints_json, AlignInOrder, AspectRatio, HardConstraint, SoftConstraint,
    SymmetricNets, UserConstraint,
};
use align_autotune::remediation::{diagnose_and_remediate, DiagnosisContext};
use align_autotune::runners::{
    extract_subckt_ports, find_top_gds, normalize_top_subckt_header, stage_design_inputs,
    IterationWorkspace,
};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

#[test]
fn test_barebones_flow() {
    let config = AutoTuneConfig {
        circuit: "test_circuit".to_string(),
        netlist_path: PathBuf::from("dummy.sp"),
        pdk_path: PathBuf::from("pdks/SKY130_PDK"),
        testbench_path: None,
        work_dir: PathBuf::from("/tmp/test_autotune"),
        max_iterations: 1,
        reference_nets: default_reference_nets(),
    };

    let result = run_autotune_loop(&config);
    assert!(result.is_ok());
}

#[test]
fn test_fom_satisfactory() {
    assert!(!FoMReport::default().is_satisfactory());

    let report = FoMReport {
        foms: vec![
            FoM {
                name: "diff_cap_imbalance_ratio".to_string(),
                value: Some(0.015),
                ngspice_required: false,
                target: 0.02,
                is_satisfied: true,
            },
            FoM {
                name: "bandwidth_retention_ratio".to_string(),
                value: Some(0.90),
                ngspice_required: true,
                target: 0.80,
                is_satisfied: true,
            },
        ],
    };
    assert!(report.is_satisfactory());

    let worse_report = FoMReport {
        foms: vec![
            FoM {
                name: "diff_cap_imbalance_ratio".to_string(),
                value: Some(0.05),
                ngspice_required: false,
                target: 0.02,
                is_satisfied: false,
            },
            FoM {
                name: "bandwidth_retention_ratio".to_string(),
                value: Some(0.75),
                ngspice_required: true,
                target: 0.80,
                is_satisfied: false,
            },
        ],
    };
    assert!(report.is_better_than(&worse_report));
}

#[test]
fn test_constraint_trait_hierarchy() {
    let soft = SymmetricNets {
        direction: "V".to_string(),
        net1: "VIN".to_string(),
        net2: "VIP".to_string(),
        pins1: None,
        pins2: None,
    };
    assert_eq!(soft.constraint_name(), "SymmetricNets");
    assert_eq!(soft.to_json()["constraint"], "SymmetricNets");

    let hard = AspectRatio {
        ratio_low: 0.5,
        ratio_high: 2.0,
        weight: Some(1),
    };
    assert!(hard.validate().is_ok());
    assert_eq!(hard.to_json()["constraint"], "AspectRatio");

    let user = AlignInOrder {
        instances: vec!["MN0".to_string(), "MN1".to_string()],
        line: "bottom".to_string(),
        direction: None,
        abut: true,
    };
    assert!(user.validate().is_ok());
    let expanded = user.expand();
    assert_eq!(expanded.len(), 3);
    assert_eq!(expanded[0].constraint_name(), "Align");
    assert_eq!(expanded[1].constraint_name(), "Order");
    assert_eq!(expanded[2].constraint_name(), "DoNotIdentify");
}

#[test]
fn test_evaluate_foms() {
    let geometry = LayoutGeometry {
        width_um: 10.0,
        height_um: 10.0,
        active_area_um2: 42.0,
        block_area_um2: 72.0,
        core_area_um2: 100.0,
        total_wirelength_um: 120.0,
        total_vias: 16,
    };

    let mut nets = HashMap::new();
    nets.insert(
        "VIP".to_string(),
        PexNet {
            name: "VIP".to_string(),
            total_c_ground: 10.0e-15,
            coupling_caps: HashMap::new(),
        },
    );
    nets.insert(
        "VIN".to_string(),
        PexNet {
            name: "VIN".to_string(),
            total_c_ground: 9.9e-15,
            coupling_caps: HashMap::new(),
        },
    );
    let extracted = ExtractedCircuit { nets };

    let mut pre_measures = HashMap::new();
    pre_measures.insert("ugbw".to_string(), 100.0e6);
    let pre_sim = SimMeasurements {
        measures: pre_measures,
    };

    let mut post_measures = HashMap::new();
    post_measures.insert("ugbw".to_string(), 90.0e6);
    let post_sim = SimMeasurements {
        measures: post_measures,
    };

    let diff_pairs = vec![("VIP".to_string(), "VIN".to_string())];
    let report = evaluate_foms(&AnalysisInputs {
        geometry: Some(&geometry),
        extracted: Some(&extracted),
        diff_pairs: &diff_pairs,
        pre_sim: Some(&pre_sim),
        post_sim: Some(&post_sim),
    });

    assert!((report.get("diff_cap_imbalance_ratio").unwrap() - 0.01).abs() < 1e-9);
    assert!((report.get("bandwidth_retention_ratio").unwrap() - 0.90).abs() < 1e-9);
    assert!((report.get("area_efficiency").unwrap() - 0.42).abs() < 1e-9);
    assert!((report.get("block_packing_efficiency").unwrap() - 0.72).abs() < 1e-9);
    assert!(report.is_satisfactory());

    let ranked = rank_net_admittances(&extracted);
    assert_eq!(ranked.len(), 2);
    assert_eq!(ranked[0].net_name, "VIP");
    assert_eq!(ranked[1].net_name, "VIN");
}

#[test]
fn test_runners_workspace_and_sanitizers() {
    let base_dir = std::env::temp_dir().join("align_autotune_runner_test");
    let _ = fs::remove_dir_all(&base_dir);

    let ws = IterationWorkspace::prepare(&base_dir, 0).unwrap();
    assert!(ws.root_dir.ends_with("iter_0"));
    assert!(ws.design_dir.is_dir());

    // Test staging netlist + constraints into iter_0/design/
    let dummy_sp = base_dir.join("source_ota.sp");
    fs::write(
        &dummy_sp,
        ".subckt ota vip vin vout vdd vss\nM0 vout vip vss vss nmos w=1u l=90n nfin=4 nf=2\n.ends ota\n",
    )
    .unwrap();

    let constraints: Vec<Box<dyn SoftConstraint>> = vec![Box::new(SymmetricNets {
        direction: "V".to_string(),
        net1: "VIP".to_string(),
        net2: "VIN".to_string(),
        pins1: None,
        pins2: None,
    })];

    let staged_sp = stage_design_inputs("ota", &dummy_sp, &constraints, &ws.design_dir).unwrap();
    assert!(staged_sp.is_file());
    assert!(ws.design_dir.join("ota.const.json").is_file());

    // Test GDS discovery prefers real .gds over .python.gds
    fs::write(ws.root_dir.join("OTA_0.python.gds"), b"py").unwrap();
    fs::write(ws.root_dir.join("OTA_0.gds"), b"real").unwrap();
    let gds = find_top_gds(&args_or_path(&ws.root_dir), "ota").unwrap();
    assert_eq!(gds.file_name().unwrap().to_str().unwrap(), "OTA_0.gds");

    // Test top-level .subckt header normalization and schematic port extraction
    let ports = extract_subckt_ports(&fs::read_to_string(&staged_sp).unwrap(), "ota");
    assert_eq!(ports, vec!["vip", "vin", "vout", "vdd", "vss"]);

    let raw_magic = "* Magic extracted\n.subckt OTA_0 VOUT VDD VSS VIN VIP\nC0 VIP 0 1.2f\n.ends\n";
    let normalized = normalize_top_subckt_header(raw_magic, "ota", &ports);
    assert!(normalized.contains(".subckt ota vip vin vout vdd vss\n"));
    assert!(normalized.trim_end().ends_with(".ends ota"));

    let _ = fs::remove_dir_all(&base_dir);
}

#[test]
fn test_parsers_spice_pnr_and_ngspice() {
    let spice_text = "\
* NGSPICE file created from five_transistor_ota.ext - technology: sky130A
.subckt five_transistor_ota vss vdd vout vinn vinp id
X0 VSS ID ID VSS sky130_fd_pr__nfet_01v8 w=1.05 l=0.15
C0 VINN VSS 2.1443f $ **FLOATING
C1 VINP VSS 2.1096f
C2 VINP VINN 4.0025f
C3 VOUT VINN -0.0075f
.ends five_transistor_ota
";
    let pex = parse_pex_spice_str(spice_text).unwrap();
    let vinp = pex.nets.get("VINP").unwrap();
    let vinn = pex.nets.get("VINN").unwrap();
    assert!((vinp.total_c_ground - 2.1096e-15).abs() < 1e-20);
    assert!((vinn.total_c_ground - 2.1443e-15).abs() < 1e-20);
    assert!((vinp.coupling_caps["VINN"] - 4.0025e-15).abs() < 1e-20);
    assert!(!vinn.coupling_caps.contains_key("VOUT")); // negative noise ignored

    // Explicit custom reference nets override the defaults
    let custom_pex = parse_pex_spice_str_with_refs(spice_text, &["sub".to_string()]).unwrap();
    assert!(custom_pex.nets.contains_key("VSS"));

    let pnr_json = r#"{
        "bbox": [0, 0, 10000, 20000],
        "terminals": [
            {"layer": "Active", "netType": "drawing", "rect": [0, 0, 10000, 5000]},
            {"layer": "M1", "netType": "drawing", "rect": [100, 0, 350, 8000]},
            {"layer": "M2", "netType": "pin", "rect": [0, 200, 6000, 480]},
            {"layer": "V0", "netType": "drawing", "rect": [100, 100, 270, 270]},
            {"layer": "V1", "netType": "drawing", "rect": [100, 200, 270, 370]}
        ]
    }"#;
    let geom = parse_pnr_layout_str(pnr_json).unwrap();
    assert!((geom.width_um - 10.0).abs() < 1e-9);
    assert!((geom.height_um - 20.0).abs() < 1e-9);
    assert!((geom.active_area_um2 - 50.0).abs() < 1e-9);
    assert!((geom.total_wirelength_um - 14.0).abs() < 1e-9);
    assert_eq!(geom.total_vias, 2);

    let ngspice_log = "\
gain_lin            =  7.53071e+00 at=  1.00000e+03
ugbw                =   6.41105e+09
Total elapsed time (seconds) = 0.127
";
    let sim = parse_ngspice_log_str(ngspice_log).unwrap();
    assert!((sim.measures["gain_lin"] - 7.53071).abs() < 1e-6);
    assert!((sim.measures["ugbw"] - 6.41105e9).abs() < 1.0);
    assert!(!sim.measures.contains_key("total elapsed time (seconds)"));
}

#[test]
fn test_remediation_escalation_and_dedup() {
    let spice_text = "\
.subckt five_transistor_ota vss vdd vout in_a in_b id
X3 vout in_b tail vss sky130_fd_pr__nfet_01v8 l=0.15 w=2.10 nf=2 m=10
X0 net8 in_a tail vss sky130_fd_pr__nfet_01v8 l=0.15 w=2.10 nf=2 m=10
C0 IN_B VSS 3.0f
C1 IN_A VSS 2.0f
C2 VOUT VSS 12.0f
C3 VOUT NET8 8.0f
.ends five_transistor_ota
";
    let pex = parse_pex_spice_str(spice_text).unwrap();
    let initial = parse_constraints_json(
        r#"[{"constraint": "PowerPorts", "ports": ["VDD"]}, {"constraint": "GroundPorts", "ports": ["VSS"]}]"#,
    )
    .unwrap();
    let refs = default_reference_nets();
    let diff_pairs = discover_diff_pairs(spice_text, "five_transistor_ota", &initial, &refs);
    assert_eq!(diff_pairs, vec![("in_b".to_string(), "in_a".to_string())]);

    let report = FoMReport {
        foms: vec![
            FoM {
                name: "diff_cap_imbalance_ratio".to_string(),
                value: Some(0.40),
                ngspice_required: false,
                target: 0.02,
                is_satisfied: false,
            },
            FoM {
                name: "bandwidth_retention_ratio".to_string(),
                value: Some(0.65),
                ngspice_required: true,
                target: 0.80,
                is_satisfied: false,
            },
        ],
    };
    // Iteration 0 -> Step 1 remediation: NetPriority(IN_B, IN_A) + NetConst(IN_B, IN_A) + NetPriority(VOUT) + NetConst(VOUT)
    let ctx0 = DiagnosisContext {
        report: &report,
        extracted: Some(&pex),
        geometry: None,
        diff_pairs: &diff_pairs,
        reference_nets: &refs,
        iteration: 0,
    };
    let step1 = diagnose_and_remediate(&ctx0, &initial);
    assert!(step1.iter().any(|c| c.constraint_name() == "NetPriority"));
    assert!(step1.iter().any(|c| c.constraint_name() == "NetConst"));

    // Iteration 1 -> Step 2 escalation: adds SymmetricNets, upgrades NetPriority weight, and adds CompactPlacement + AspectRatio
    let ctx1 = DiagnosisContext {
        iteration: 1,
        ..ctx0
    };
    let step2 = diagnose_and_remediate(&ctx1, &step1);
    let sym_count = step2
        .iter()
        .filter(|c| c.constraint_name() == "SymmetricNets")
        .count();
    assert_eq!(sym_count, 1);
    assert!(step2
        .iter()
        .any(|c| c.constraint_name() == "CompactPlacement"));
    assert!(step2.iter().any(|c| c.constraint_name() == "AspectRatio"));
}

fn args_or_path(p: &std::path::Path) -> PathBuf {
    p.to_path_buf()
}
