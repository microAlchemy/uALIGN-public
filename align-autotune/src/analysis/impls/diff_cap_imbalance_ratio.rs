use crate::analysis::foms::{AnalysisInputs, FigureOfMerit};
use crate::analysis::parasitics::calculate_differential_imbalance;
use crate::remediation::constraints::{NetConst, NetPriority, SoftConstraint, SymmetricNets};
use crate::remediation::diagnoser::{select_shield_net, DiagnosisContext};

#[derive(Debug, Default, Clone, Copy)]
pub struct DiffCapImbalanceRatio;

impl FigureOfMerit for DiffCapImbalanceRatio {
    fn target(&self) -> f64 {
        0.02
    }

    fn is_satisfied(&self, val: f64) -> bool {
        val <= self.target()
    }

    fn name(&self) -> &'static str {
        "diff_cap_imbalance_ratio"
    }

    fn ngspice_required(&self) -> bool {
        false
    }

    fn run(&self, inputs: &AnalysisInputs<'_>) -> Option<f64> {
        if let Some(circuit) = inputs.extracted {
            let worst = inputs
                .diff_pairs
                .iter()
                .filter_map(|(net_p, net_n)| {
                    calculate_differential_imbalance(circuit, net_p, net_n)
                })
                .fold(None, |acc: Option<f64>, val| {
                    Some(acc.map_or(val, |curr| curr.max(val)))
                });

            if worst.is_some() {
                return worst;
            }
        }

        inputs
            .post_sim
            .and_then(|sim| sim.measures.get("diff_imb").copied())
    }

    fn remediate(
        &self,
        ctx: &DiagnosisContext<'_>,
        current_constraints: &[Box<dyn SoftConstraint>],
    ) -> Vec<Box<dyn SoftConstraint>> {
        let mut proposals: Vec<Box<dyn SoftConstraint>> = Vec::new();
        let shield_net = select_shield_net(current_constraints, ctx.reference_nets, ctx.extracted);
        let target = self.target();

        for (net_p, net_n) in ctx.diff_pairs {
            let pair_violated = if let Some(ext) = ctx.extracted {
                calculate_differential_imbalance(ext, net_p, net_n).map_or(true, |imb| imb > target)
            } else {
                true
            };

            if !pair_violated {
                continue;
            }

            let p_up = net_p.to_ascii_uppercase();
            let n_up = net_n.to_ascii_uppercase();

            if ctx.iteration == 0 {
                proposals.push(Box::new(NetPriority {
                    nets: vec![p_up.clone(), n_up.clone()],
                    weight: 10,
                }));
                proposals.push(Box::new(NetConst {
                    nets: vec![p_up, n_up],
                    shield: shield_net.clone(),
                    criticality: Some(10),
                }));
            } else {
                proposals.push(Box::new(SymmetricNets {
                    direction: "V".to_string(),
                    net1: p_up.clone(),
                    net2: n_up.clone(),
                    pins1: None,
                    pins2: None,
                }));
                proposals.push(Box::new(NetPriority {
                    nets: vec![p_up.clone(), n_up.clone()],
                    weight: 20,
                }));
                proposals.push(Box::new(NetConst {
                    nets: vec![p_up, n_up],
                    shield: shield_net.clone(),
                    criticality: Some(20),
                }));
            }
        }

        proposals
    }
}
