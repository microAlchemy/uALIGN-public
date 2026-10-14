use serde::{Deserialize, Serialize};

use crate::config::TargetFoMs;

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
