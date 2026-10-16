use serde::{Deserialize, Serialize};
use std::fmt;

// Add new FoM implementations here and add them to the `LIST_OF_FOMS` below.
use crate::analysis::impls::{
    AreaEfficiency, BandwidthRetentionRatio, BlockPackingEfficiency, DiffCapImbalanceRatio,
};
use crate::parsers::ngspice_out::SimMeasurements;
use crate::parsers::pnr_json::LayoutGeometry;
use crate::parsers::spice::ExtractedCircuit;
use crate::remediation::constraints::SoftConstraint;
use crate::remediation::diagnoser::DiagnosisContext;

/// Static registry of all active Figures of Merit.
pub const LIST_OF_FOMS: &[&dyn FigureOfMerit] = &[
    &DiffCapImbalanceRatio,
    &BandwidthRetentionRatio,
    &AreaEfficiency,
    &BlockPackingEfficiency,
];

/// Inputs collected from a single layout + PEX + simulation iteration.
pub struct AnalysisInputs<'a> {
    pub geometry: Option<&'a LayoutGeometry>,
    pub extracted: Option<&'a ExtractedCircuit>,
    pub diff_pairs: &'a [(String, String)],
    pub pre_sim: Option<&'a SimMeasurements>,
    pub post_sim: Option<&'a SimMeasurements>,
}

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct FoM {
    pub name: String,
    pub value: Option<f64>,
    pub ngspice_required: bool,
    pub target: f64,
    pub is_satisfied: bool,
}

impl FoM {
    pub fn violation_penalty(&self) -> f64 {
        if self.is_satisfied || self.target <= 0.0 {
            return 0.0;
        }
        self.value
            .map_or(0.0, |val| (val - self.target).abs() / self.target)
    }
}

pub trait FigureOfMerit: Sync {
    fn name(&self) -> &'static str;
    fn ngspice_required(&self) -> bool;
    fn target(&self) -> f64;
    fn is_satisfied(&self, val: f64) -> bool;
    fn run(&self, inputs: &AnalysisInputs<'_>) -> Option<f64>;
    fn remediate(
        &self,
        _ctx: &DiagnosisContext<'_>,
        _current_constraints: &[Box<dyn SoftConstraint>],
    ) -> Vec<Box<dyn SoftConstraint>> {
        Vec::new()
    }
    fn evaluate(&self, inputs: &AnalysisInputs<'_>) -> FoM {
        let value = self.run(inputs);
        let is_satisfied = value.map_or(true, |v| self.is_satisfied(v));
        FoM {
            name: self.name().to_string(),
            value,
            ngspice_required: self.ngspice_required(),
            target: self.target(),
            is_satisfied,
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct FoMReport {
    pub foms: Vec<FoM>,
}

impl FoMReport {
    /// Looks up an evaluated FoM value by metric name.
    pub fn get(&self, name: &str) -> Option<f64> {
        self.foms
            .iter()
            .find(|f| f.name.eq_ignore_ascii_case(name))
            .and_then(|f| f.value)
    }

    /// Returns true if at least one FoM was evaluated.
    pub fn has_measurements(&self) -> bool {
        self.foms.iter().any(|fom| fom.value.is_some())
    }

    /// Checks whether all evaluated metrics in `self.foms` meet their respective targets.
    pub fn is_satisfactory(&self) -> bool {
        if !self.has_measurements() {
            return false;
        }
        self.foms.iter().all(|fom| fom.is_satisfied)
    }

    /// Computes total normalized violation penalty across all evaluated FoMs in `self.foms`.
    pub fn violation_score(&self) -> f64 {
        self.foms.iter().map(|fom| fom.violation_penalty()).sum()
    }

    /// Returns `true` if `self` improves over `other` across `self.foms`.
    pub fn is_better_than(&self, other: &Self) -> bool {
        if !self.has_measurements() {
            return false;
        }
        if !other.has_measurements() {
            return true;
        }
        // Reject if any previously measured metric became None
        for other_fom in &other.foms {
            if other_fom.value.is_some() && self.get(&other_fom.name).is_none() {
                return false;
            }
        }

        let self_score = self.violation_score();
        let other_score = other.violation_score();
        self_score + 1e-9 < other_score
    }
}

impl fmt::Display for FoMReport {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let parts: Vec<String> = self
            .foms
            .iter()
            .map(|fom| format!("{}={}", fom.name, fmt_sig_figs(fom.value, 3)))
            .collect();
        write!(f, "{}", parts.join(", "))
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

/// Runs all registered Figures of Merit in [`LIST_OF_FOMS`] and returns a unified [`FoMReport`].
pub fn evaluate_foms(inputs: &AnalysisInputs<'_>) -> FoMReport {
    let foms = LIST_OF_FOMS
        .iter()
        .map(|metric| metric.evaluate(inputs))
        .collect();
    FoMReport { foms }
}
