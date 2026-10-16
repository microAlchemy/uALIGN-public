use crate::analysis::foms::{AnalysisInputs, FigureOfMerit};

/// Pure silicon diffusion density: `sum(Active) / (width_um * height_um)`.
#[derive(Debug, Default, Clone, Copy)]
pub struct AreaEfficiency;

impl FigureOfMerit for AreaEfficiency {
    fn target(&self) -> f64 {
        0.10
    }

    fn is_satisfied(&self, val: f64) -> bool {
        val >= self.target()
    }

    fn name(&self) -> &'static str {
        "area_efficiency"
    }

    fn ngspice_required(&self) -> bool {
        false
    }

    fn run(&self, inputs: &AnalysisInputs<'_>) -> Option<f64> {
        let geom = inputs.geometry?;
        let bbox_area = geom.width_um * geom.height_um;
        if bbox_area > 0.0 {
            Some(geom.active_area_um2 / bbox_area)
        } else {
            None
        }
    }
}
