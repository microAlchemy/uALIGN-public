pub mod foms;
pub mod parasitics;

pub use foms::FoMReport;
pub use parasitics::{
    calculate_differential_imbalance, find_net, rank_net_admittances, total_net_capacitance,
    NetAdmittance,
};

use crate::parsers::ngspice_out::SimMeasurements;
use crate::parsers::pnr_json::LayoutGeometry;
use crate::parsers::spice::ExtractedCircuit;

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
