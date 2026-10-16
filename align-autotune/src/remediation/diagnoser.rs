use crate::analysis::foms::{FoMReport, LIST_OF_FOMS};
use crate::analysis::parasitics::find_net;
use crate::parsers::pnr_json::LayoutGeometry;
use crate::parsers::spice::ExtractedCircuit;
use crate::remediation::constraints::{AspectRatio, SoftConstraint};

/// White-box context combining FoMs, extracted parasitics, layout geometry, and circuit topology.
#[derive(Debug, Clone)]
pub struct DiagnosisContext<'a> {
    pub report: &'a FoMReport,
    pub extracted: Option<&'a ExtractedCircuit>,
    pub geometry: Option<&'a LayoutGeometry>,
    pub diff_pairs: &'a [(String, String)],
    pub reference_nets: &'a [String],
    pub iteration: usize,
}

/// Iterates over all registered Figures of Merit in [`LIST_OF_FOMS`], invokes each violated
/// metric's `remediate` implementation, and deduplicates the resulting constraints.
pub fn diagnose_and_remediate(
    ctx: &DiagnosisContext<'_>,
    current_constraints: &[Box<dyn SoftConstraint>],
) -> Vec<Box<dyn SoftConstraint>> {
    let mut proposals: Vec<Box<dyn SoftConstraint>> = Vec::new();

    for metric in LIST_OF_FOMS {
        if let Some(fom) = ctx
            .report
            .foms
            .iter()
            .find(|f| f.name.eq_ignore_ascii_case(metric.name()))
        {
            if !fom.is_satisfied {
                proposals.extend(metric.remediate(ctx, current_constraints));
            }
        }
    }

    merge_and_dedup_constraints(current_constraints, proposals)
}

/// Contracts the current bounding-box aspect ratio `r = W / H` halfway toward `1.0` (square)
/// in log-space: `[sqrt(s), 1 / sqrt(s)]` where `s = min(W/H, H/W)`.
pub fn feasible_aspect_ratio(geometry: Option<&LayoutGeometry>) -> AspectRatio {
    let s = geometry
        .filter(|g| g.width_um > 0.0 && g.height_um > 0.0)
        .map(|g| {
            let r = g.width_um / g.height_um;
            r.min(1.0 / r)
        })
        .unwrap_or(0.5);

    let root = s.sqrt();
    let low = (root * 100.0).round() / 100.0;
    let high = ((1.0 / root) * 100.0).round() / 100.0;

    AspectRatio {
        ratio_low: low,
        ratio_high: high.max(low + 0.01),
        weight: Some(10),
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

pub fn has_net_priority(constraints: &[Box<dyn SoftConstraint>], net: &str) -> bool {
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

pub fn is_ref_or_diff_net(
    net: &str,
    reference_nets: &[String],
    diff_pairs: &[(String, String)],
) -> bool {
    if net.contains('#')
        || net.eq_ignore_ascii_case("0")
        || reference_nets.iter().any(|r| r.eq_ignore_ascii_case(net))
    {
        return true;
    }
    diff_pairs
        .iter()
        .any(|(p, n)| p.eq_ignore_ascii_case(net) || n.eq_ignore_ascii_case(net))
}

pub fn select_shield_net(
    constraints: &[Box<dyn SoftConstraint>],
    reference_nets: &[String],
    extracted: Option<&ExtractedCircuit>,
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
            && extracted.is_some_and(|ext| find_net(ext, candidate).is_some())
        {
            return Some(candidate.to_ascii_uppercase());
        }
    }
    None
}
