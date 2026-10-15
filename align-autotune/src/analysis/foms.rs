use serde::{Deserialize, Serialize};

use crate::analysis::parasitics::calculate_differential_imbalance;
use crate::config::TargetFoMs;
use crate::parsers::ngspice_out::SimMeasurements;
use crate::parsers::pnr_json::LayoutGeometry;
use crate::parsers::spice::ExtractedCircuit;

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct FoMReport {
    pub diff_cap_imbalance_ratio: Option<f64>,
    pub bandwidth_retention_ratio: Option<f64>,
    pub area_efficiency: Option<f64>,
}

impl FoMReport {
    /// Returns true if at least one primary FoM was evaluated.
    pub fn has_measurements(&self) -> bool {
        self.diff_cap_imbalance_ratio.is_some() || self.bandwidth_retention_ratio.is_some()
    }

    /// Checks whether evaluated metrics meet the target thresholds.
    /// Returns `false` on an empty report (`None` for both primary metrics).
    pub fn is_satisfactory(&self, target_diff_cap: f64, target_bw_retention: f64) -> bool {
        if !self.has_measurements() {
            return false;
        }
        let diff_ok = self
            .diff_cap_imbalance_ratio
            .map_or(true, |v| v <= target_diff_cap);
        let bw_ok = self
            .bandwidth_retention_ratio
            .map_or(true, |v| v >= target_bw_retention);
        diff_ok && bw_ok
    }

    /// Convenience check against a `TargetFoMs` config struct.
    pub fn meets_targets(&self, targets: &TargetFoMs) -> bool {
        self.is_satisfactory(
            targets.max_diff_cap_imbalance,
            targets.min_bandwidth_retention,
        )
    }

    /// Computes total normalized violation penalty relative to `targets` (0.0 means all targets met).
    pub fn violation_score(&self, targets: &TargetFoMs) -> f64 {
        let diff_penalty = self.diff_cap_imbalance_ratio.map_or(0.0, |v| {
            if v > targets.max_diff_cap_imbalance && targets.max_diff_cap_imbalance > 0.0 {
                (v - targets.max_diff_cap_imbalance) / targets.max_diff_cap_imbalance
            } else {
                0.0
            }
        });

        let bw_penalty = self.bandwidth_retention_ratio.map_or(0.0, |v| {
            if v < targets.min_bandwidth_retention && targets.min_bandwidth_retention > 0.0 {
                (targets.min_bandwidth_retention - v) / targets.min_bandwidth_retention
            } else {
                0.0
            }
        });

        diff_penalty + bw_penalty
    }

    /// Returns `true` if `self` improves over `other` (lower violation score, or equal score with higher area efficiency).
    pub fn is_better_than(&self, other: &Self, targets: &TargetFoMs) -> bool {
        if !self.has_measurements() {
            return false;
        }
        if !other.has_measurements() {
            return true;
        }

        let self_score = self.violation_score(targets);
        let other_score = other.violation_score(targets);

        if (self_score - other_score).abs() > 1e-9 {
            self_score < other_score
        } else {
            self.area_efficiency.unwrap_or(0.0) > other.area_efficiency.unwrap_or(0.0)
        }
    }
}

/// Inputs collected from a single layout + PEX + simulation iteration.
pub struct AnalysisInputs<'a> {
    pub geometry: Option<&'a LayoutGeometry>,
    pub extracted: Option<&'a ExtractedCircuit>,
    pub diff_pairs: &'a [(String, String)],
    pub pre_sim: Option<&'a SimMeasurements>,
    pub post_sim: Option<&'a SimMeasurements>,
}

/// Aggregate geometric, parasitic, and electrical measurements into a unified `FoMReport`.
pub fn evaluate_foms(inputs: &AnalysisInputs<'_>) -> FoMReport {
    let diff_cap_imbalance_ratio =
        compute_worst_diff_imbalance(inputs.extracted, inputs.diff_pairs, inputs.post_sim);

    let bandwidth_retention_ratio = compute_bandwidth_retention(inputs.pre_sim, inputs.post_sim);

    let area_efficiency = inputs.geometry.and_then(|geom| {
        let bbox_area = geom.width_um * geom.height_um;
        if bbox_area > 0.0 {
            Some(geom.active_area_um2 / bbox_area)
        } else {
            None
        }
    });

    FoMReport {
        diff_cap_imbalance_ratio,
        bandwidth_retention_ratio,
        area_efficiency,
    }
}

fn compute_worst_diff_imbalance(
    extracted: Option<&ExtractedCircuit>,
    diff_pairs: &[(String, String)],
    post_sim: Option<&SimMeasurements>,
) -> Option<f64> {
    if let Some(circuit) = extracted {
        let worst = diff_pairs
            .iter()
            .filter_map(|(net_p, net_n)| calculate_differential_imbalance(circuit, net_p, net_n))
            .fold(None, |acc: Option<f64>, val| {
                Some(acc.map_or(val, |curr| curr.max(val)))
            });

        if worst.is_some() {
            return worst;
        }
    }

    post_sim.and_then(|sim| sim.measures.get("diff_imb").copied())
}

fn compute_bandwidth_retention(
    pre_sim: Option<&SimMeasurements>,
    post_sim: Option<&SimMeasurements>,
) -> Option<f64> {
    let ugbw_pre = *pre_sim?.measures.get("ugbw")?;
    let ugbw_post = *post_sim?.measures.get("ugbw")?;

    if ugbw_pre > 0.0 {
        Some(ugbw_post / ugbw_pre)
    } else {
        None
    }
}
