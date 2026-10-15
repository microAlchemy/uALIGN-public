use serde::{Deserialize, Serialize};
use std::fmt;

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
    // TODO: make this more scalable for arbitary new foms
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
        if (other.diff_cap_imbalance_ratio.is_some() && self.diff_cap_imbalance_ratio.is_none())
            || (other.bandwidth_retention_ratio.is_some()
                && self.bandwidth_retention_ratio.is_none())
        {
            return false;
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

impl fmt::Display for FoMReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "diff_cap_imbalance={}, bw_retention={}, area_efficiency={}",
            fmt_sig_figs(self.diff_cap_imbalance_ratio, 3),
            fmt_sig_figs(self.bandwidth_retention_ratio, 3),
            fmt_sig_figs(self.area_efficiency, 3),
        )
    }
}

fn fmt_sig_figs(val: Option<f64>, sig_figs: usize) -> String {
    let Some(x) = val else {
        return "N/A".to_string();
    };
    if x == 0.0 || !x.is_finite() {
        return format!("{x:.3}");
    }
    let exp = x.abs().log10().floor() as i32;
    let decimals = (sig_figs as i32 - 1 - exp).max(0) as usize;
    format!("{x:.decimals$}")
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

    let bandwidth_retention_ratio: Option<f64> = compute_bandwidth_retention(inputs.pre_sim, inputs.post_sim);

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
    let pre = &pre_sim?.measures;
    let post = &post_sim?.measures;

    let ugbw_pre =
        get_measure_ci(pre, "ugbw").or_else(|| get_measure_ci(pre, "ugbw_mhz").map(|v| v * 1e6));
    let ugbw_post =
        get_measure_ci(post, "ugbw").or_else(|| get_measure_ci(post, "ugbw_mhz").map(|v| v * 1e6));

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

fn get_measure_ci(measures: &std::collections::HashMap<String, f64>, key: &str) -> Option<f64> {
    measures.get(key).copied().or_else(|| {
        measures
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(key))
            .map(|(_, &v)| v)
    })
}
