use std::collections::HashMap;

use crate::analysis::foms::{AnalysisInputs, FigureOfMerit};
use crate::analysis::parasitics::{find_net, rank_net_admittances};
use crate::remediation::constraints::{CompactPlacement, NetConst, NetPriority, SoftConstraint};
use crate::remediation::diagnoser::{
    feasible_aspect_ratio, has_net_priority, is_ref_or_diff_net, select_shield_net,
    DiagnosisContext,
};

#[derive(Debug, Default, Clone, Copy)]
pub struct BandwidthRetentionRatio;

impl FigureOfMerit for BandwidthRetentionRatio {
    fn target(&self) -> f64 {
        0.85
    }

    fn is_satisfied(&self, val: f64) -> bool {
        val >= self.target()
    }

    fn name(&self) -> &'static str {
        "bandwidth_retention_ratio"
    }

    fn ngspice_required(&self) -> bool {
        true
    }

    fn run(&self, inputs: &AnalysisInputs<'_>) -> Option<f64> {
        let pre = &inputs.pre_sim?.measures;
        let post = &inputs.post_sim?.measures;

        let ugbw_pre = get_measure_ci(pre, "ugbw")
            .or_else(|| get_measure_ci(pre, "ugbw_mhz").map(|v| v * 1e6));
        let ugbw_post = get_measure_ci(post, "ugbw")
            .or_else(|| get_measure_ci(post, "ugbw_mhz").map(|v| v * 1e6));

        if let (Some(u_pre), Some(u_post)) = (ugbw_pre, ugbw_post) {
            if u_pre > 0.0 {
                return Some(u_post / u_pre);
            }
        }

        // Fallback for transient testbenches (e.g. buffer) measuring propagation delay (tphl_ns, tplh_ns)
        if let (Some(hl_pre), Some(lh_pre), Some(hl_post), Some(lh_post)) = (
            get_measure_ci(pre, "tphl_ns"),
            get_measure_ci(pre, "tplh_ns"),
            get_measure_ci(post, "tphl_ns"),
            get_measure_ci(post, "tplh_ns"),
        ) {
            let delay_pre = hl_pre + lh_pre;
            let delay_post = hl_post + lh_post;
            if delay_pre > 0.0 && delay_post > 0.0 {
                return Some(delay_pre / delay_post);
            }
        }

        None
    }

    fn remediate(
        &self,
        ctx: &DiagnosisContext<'_>,
        current_constraints: &[Box<dyn SoftConstraint>],
    ) -> Vec<Box<dyn SoftConstraint>> {
        let mut proposals: Vec<Box<dyn SoftConstraint>> = Vec::new();
        let shield_net = select_shield_net(current_constraints, ctx.reference_nets, ctx.extracted);
        let mut added_net_remediation = false;

        if let Some(extracted) = ctx.extracted {
            let ranked = rank_net_admittances(extracted);
            let candidates: Vec<_> = ranked
                .into_iter()
                .filter(|r| !is_ref_or_diff_net(&r.net_name, ctx.reference_nets, ctx.diff_pairs))
                .collect();

            let total_sig_cap: f64 = candidates.iter().map(|r| r.capacitance).sum();

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

                if ctx.iteration == 0 && !has_net_priority(current_constraints, &net_up) {
                    proposals.push(Box::new(NetPriority {
                        nets: vec![net_up.clone()],
                        weight: 10,
                    }));
                    if coupling_ratio > 0.35 {
                        proposals.push(Box::new(NetConst {
                            nets: vec![net_up],
                            shield: shield_net.clone(),
                            criticality: Some(10),
                        }));
                    }
                    added_net_remediation = true;
                } else {
                    proposals.push(Box::new(NetPriority {
                        nets: vec![net_up.clone()],
                        weight: 25,
                    }));
                    proposals.push(Box::new(NetConst {
                        nets: vec![net_up],
                        shield: shield_net.clone(),
                        criticality: Some(25),
                    }));
                    proposals.push(Box::new(CompactPlacement {
                        style: "center".to_string(),
                    }));
                    proposals.push(Box::new(feasible_aspect_ratio(ctx.geometry)));
                    added_net_remediation = true;
                }
            }
        }

        if !added_net_remediation {
            proposals.push(Box::new(CompactPlacement {
                style: "center".to_string(),
            }));
            proposals.push(Box::new(feasible_aspect_ratio(ctx.geometry)));
        }

        proposals
    }
}

fn get_measure_ci(measures: &HashMap<String, f64>, key: &str) -> Option<f64> {
    measures.get(key).copied().or_else(|| {
        measures
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(key))
            .map(|(_, &v)| v)
    })
}
