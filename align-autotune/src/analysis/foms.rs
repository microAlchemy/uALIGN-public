use serde::{Deserialize, Serialize};

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct FoMReport {
    pub diff_cap_imbalance_ratio: Option<f64>,
    pub bandwidth_retention_ratio: Option<f64>,
    pub area_efficiency: Option<f64>, // add more as needed
}

impl FoMReport {
    pub fn is_satisfactory(&self, target_diff_cap: f64, target_bw_retention: f64) -> bool {
        let diff_ok = self
            .diff_cap_imbalance_ratio
            .map_or(true, |v| v <= target_diff_cap);
        let bw_ok = self
            .bandwidth_retention_ratio
            .map_or(true, |v| v >= target_bw_retention);
        diff_ok && bw_ok
    }
}
