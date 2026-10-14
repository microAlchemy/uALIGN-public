use crate::analysis::foms::FoMReport;
use crate::analysis::parasitics::{
    calculate_differential_imbalance, find_net, rank_net_admittances,
};
use crate::config::TargetFoMs;
use crate::parsers::pnr_json::LayoutGeometry;
use crate::parsers::spice::ExtractedCircuit;
use crate::remediation::constraints::{
    AspectRatio, CompactPlacement, NetConst, NetPriority, SoftConstraint, SymmetricNets,
};

/// White-box context combining FoMs, extracted parasitics, layout geometry, and circuit topology.
#[derive(Debug, Clone)]
pub struct DiagnosisContext<'a> {
    pub report: &'a FoMReport,
    pub targets: &'a TargetFoMs,
    pub extracted: Option<&'a ExtractedCircuit>,
    pub geometry: Option<&'a LayoutGeometry>,
    pub diff_pairs: &'a [(String, String)],
    pub reference_nets: &'a [String],
    pub iteration: usize,
}

/// Diagnoses FoM violations using white-box PEX/layout data and synthesizes updated ALIGN constraints.
pub fn diagnose_and_remediate(
    ctx: &DiagnosisContext<'_>,
    current_constraints: &[Box<dyn SoftConstraint>],
) -> Vec<Box<dyn SoftConstraint>> {
    let mut proposals: Vec<Box<dyn SoftConstraint>> = Vec::new();
    let shield_net = select_shield_net(current_constraints, ctx.reference_nets);

    if let Some(imbalance) = ctx.report.diff_cap_imbalance_ratio {
        if imbalance > ctx.targets.max_diff_cap_imbalance {
            remediate_diff_imbalance(
                ctx,
                current_constraints,
                shield_net.as_deref(),
                &mut proposals,
            );
        }
    }

    if let Some(bw_ret) = ctx.report.bandwidth_retention_ratio {
        if bw_ret < ctx.targets.min_bandwidth_retention {
            remediate_bandwidth_loss(
                ctx,
                current_constraints,
                shield_net.as_deref(),
                &mut proposals,
            );
        }
    }

    merge_and_dedup_constraints(current_constraints, proposals)
}

fn remediate_diff_imbalance(
    ctx: &DiagnosisContext<'_>,
    current_constraints: &[Box<dyn SoftConstraint>],
    shield_net: Option<&str>,
    proposals: &mut Vec<Box<dyn SoftConstraint>>,
) {
    for (net_p, net_n) in ctx.diff_pairs {
        let pair_violated = if let Some(ext) = ctx.extracted {
            calculate_differential_imbalance(ext, net_p, net_n)
                .map_or(true, |imb| imb > ctx.targets.max_diff_cap_imbalance)
        } else {
            true
        };

        if !pair_violated {
            continue;
        }

        let p_up = net_p.to_ascii_uppercase();
        let n_up = net_n.to_ascii_uppercase();

        if !has_symmetric_nets(current_constraints, &p_up, &n_up) {
            // Escalation Step 1: Enforce net-level routing symmetry and elevate HPWL placement weight
            proposals.push(Box::new(SymmetricNets {
                direction: "V".to_string(),
                net1: p_up.clone(),
                net2: n_up.clone(),
                pins1: None,
                pins2: None,
            }));
            proposals.push(Box::new(NetPriority {
                nets: vec![p_up, n_up],
                weight: 10,
            }));
        } else {
            // Escalation Step 2: SymmetricNets already active; add router shielding & higher criticality
            proposals.push(Box::new(NetPriority {
                nets: vec![p_up.clone(), n_up.clone()],
                weight: 20,
            }));
            proposals.push(Box::new(NetConst {
                nets: vec![p_up, n_up],
                shield: shield_net.map(|s| s.to_string()),
                criticality: Some(20),
            }));
        }
    }
}

fn remediate_bandwidth_loss(
    ctx: &DiagnosisContext<'_>,
    current_constraints: &[Box<dyn SoftConstraint>],
    shield_net: Option<&str>,
    proposals: &mut Vec<Box<dyn SoftConstraint>>,
) {
    let mut added_net_remediation = false;

    if let Some(extracted) = ctx.extracted {
        let ranked = rank_net_admittances(extracted);
        let candidates: Vec<_> = ranked
            .into_iter()
            .filter(|r| !is_ref_or_diff_net(&r.net_name, ctx.reference_nets, ctx.diff_pairs))
            .collect();

        let total_sig_cap: f64 = candidates.iter().map(|r| r.capacitance).sum();

        // Focus on at most top 2 dominant nets (>= 10% of signal capacitance, or top-1 fallback)
        let mut selected: Vec<_> = candidates
            .iter()
            .filter(|r| total_sig_cap > 0.0 && (r.capacitance / total_sig_cap) >= 0.10)
            .take(2)
            .collect();
        if selected.is_empty() {
            if let Some(first) = candidates.first() {
                selected.push(first);
            }
        }

        for culprit in selected {
            let net_up = culprit.net_name.to_ascii_uppercase();
            let coupling_ratio = find_net(extracted, &culprit.net_name)
                .map(|net| {
                    let coup: f64 = net.coupling_caps.values().sum();
                    if culprit.capacitance > 0.0 {
                        coup / culprit.capacitance
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0);

            if !has_net_priority(current_constraints, &net_up) {
                // Step 1: Penalize HPWL on the dominant parasitic net
                proposals.push(Box::new(NetPriority {
                    nets: vec![net_up.clone()],
                    weight: 10,
                }));
                if coupling_ratio > 0.35 {
                    proposals.push(Box::new(NetConst {
                        nets: vec![net_up],
                        shield: shield_net.map(|s| s.to_string()),
                        criticality: Some(10),
                    }));
                }
                added_net_remediation = true;
            } else {
                // Step 2: Escalate net priority + router criticality + center compaction
                proposals.push(Box::new(NetPriority {
                    nets: vec![net_up.clone()],
                    weight: 25,
                }));
                proposals.push(Box::new(NetConst {
                    nets: vec![net_up],
                    shield: shield_net.map(|s| s.to_string()),
                    criticality: Some(25),
                }));
                proposals.push(Box::new(CompactPlacement {
                    style: "center".to_string(),
                }));
                proposals.push(Box::new(AspectRatio {
                    ratio_low: 0.75,
                    ratio_high: 1.33,
                    weight: Some(10),
                }));
                added_net_remediation = true;
            }
        }
    }

    if !added_net_remediation {
        proposals.push(Box::new(CompactPlacement {
            style: "center".to_string(),
        }));
        proposals.push(Box::new(AspectRatio {
            ratio_low: 0.75,
            ratio_high: 1.33,
            weight: Some(10),
        }));
    }
}

/// Merges newly proposed constraints into `current_constraints`, upgrading existing entries in place
/// instead of emitting duplicate constraints.
pub fn merge_and_dedup_constraints(
    current_constraints: &[Box<dyn SoftConstraint>],
    proposals: Vec<Box<dyn SoftConstraint>>,
) -> Vec<Box<dyn SoftConstraint>> {
    let mut merged: Vec<Box<dyn SoftConstraint>> = current_constraints.to_vec();

    for prop in proposals {
        let prop_json = prop.to_json();
        let name = prop.constraint_name();

        let mut replaced = false;
        for existing in &mut merged {
            if existing.constraint_name() != name {
                continue;
            }
            let ex_json = existing.to_json();
            match name {
                "CompactPlacement" | "AspectRatio" => {
                    *existing = prop.clone_box();
                    replaced = true;
                    break;
                }
                "NetPriority" | "NetConst" => {
                    if ex_json.get("nets") == prop_json.get("nets") {
                        *existing = prop.clone_box();
                        replaced = true;
                        break;
                    }
                }
                "SymmetricNets" => {
                    let ex_n1 = ex_json.get("net1").and_then(|v| v.as_str()).unwrap_or("");
                    let ex_n2 = ex_json.get("net2").and_then(|v| v.as_str()).unwrap_or("");
                    let pr_n1 = prop_json.get("net1").and_then(|v| v.as_str()).unwrap_or("");
                    let pr_n2 = prop_json.get("net2").and_then(|v| v.as_str()).unwrap_or("");
                    if (ex_n1.eq_ignore_ascii_case(pr_n1) && ex_n2.eq_ignore_ascii_case(pr_n2))
                        || (ex_n1.eq_ignore_ascii_case(pr_n2) && ex_n2.eq_ignore_ascii_case(pr_n1))
                    {
                        replaced = true;
                        break;
                    }
                }
                _ => {
                    if ex_json == prop_json {
                        replaced = true;
                        break;
                    }
                }
            }
        }

        if !replaced {
            merged.push(prop);
        }
    }

    merged
}

fn has_symmetric_nets(constraints: &[Box<dyn SoftConstraint>], net1: &str, net2: &str) -> bool {
    constraints.iter().any(|c| {
        if c.constraint_name() != "SymmetricNets" {
            return false;
        }
        let v = c.to_json();
        let n1 = v.get("net1").and_then(|x| x.as_str()).unwrap_or("");
        let n2 = v.get("net2").and_then(|x| x.as_str()).unwrap_or("");
        (n1.eq_ignore_ascii_case(net1) && n2.eq_ignore_ascii_case(net2))
            || (n1.eq_ignore_ascii_case(net2) && n2.eq_ignore_ascii_case(net1))
    })
}

fn has_net_priority(constraints: &[Box<dyn SoftConstraint>], net: &str) -> bool {
    constraints.iter().any(|c| {
        if c.constraint_name() != "NetPriority" {
            return false;
        }
        c.to_json()
            .get("nets")
            .and_then(|v| v.as_array())
            .is_some_and(|arr| {
                arr.iter()
                    .any(|item| item.as_str().is_some_and(|s| s.eq_ignore_ascii_case(net)))
            })
    })
}

fn is_ref_or_diff_net(
    net: &str,
    reference_nets: &[String],
    diff_pairs: &[(String, String)],
) -> bool {
    if net.eq_ignore_ascii_case("0") || reference_nets.iter().any(|r| r.eq_ignore_ascii_case(net)) {
        return true;
    }
    diff_pairs
        .iter()
        .any(|(p, n)| p.eq_ignore_ascii_case(net) || n.eq_ignore_ascii_case(net))
}

fn select_shield_net(
    constraints: &[Box<dyn SoftConstraint>],
    reference_nets: &[String],
) -> Option<String> {
    for c in constraints {
        if c.constraint_name() == "GroundPorts" {
            if let Some(port) = c
                .to_json()
                .get("ports")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|v| v.as_str())
            {
                return Some(port.to_ascii_uppercase());
            }
        }
    }
    for candidate in ["vss", "gnd", "vgnd"] {
        if reference_nets
            .iter()
            .any(|r| r.eq_ignore_ascii_case(candidate))
        {
            return Some(candidate.to_ascii_uppercase());
        }
    }
    None
}
