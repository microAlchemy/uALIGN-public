use crate::analysis::foms::{AnalysisInputs, FigureOfMerit};
use crate::remediation::constraints::{CompactPlacement, SoftConstraint};
use crate::remediation::diagnoser::{feasible_aspect_ratio, DiagnosisContext};

/// Floorplanner block packing utilization: `sum(Block envelopes) / core_bbox_area`.
/// Measures how tightly the C++ placer packed primitive macro blocks (1 - whitespace).
#[derive(Debug, Default, Clone, Copy)]
pub struct BlockPackingEfficiency;

impl FigureOfMerit for BlockPackingEfficiency {
    fn target(&self) -> f64 {
        0.65
    }

    fn is_satisfied(&self, val: f64) -> bool {
        val >= self.target()
    }

    fn name(&self) -> &'static str {
        "block_packing_efficiency"
    }

    fn ngspice_required(&self) -> bool {
        false
    }

    fn run(&self, inputs: &AnalysisInputs<'_>) -> Option<f64> {
        let geom = inputs.geometry?;
        let denom = if geom.core_area_um2 > 0.0 {
            geom.core_area_um2
        } else {
            geom.width_um * geom.height_um
        };
        if denom > 0.0 && geom.block_area_um2 > 0.0 {
            Some((geom.block_area_um2 / denom).min(1.0))
        } else {
            None
        }
    }

    fn remediate(
        &self,
        ctx: &DiagnosisContext<'_>,
        _current_constraints: &[Box<dyn SoftConstraint>],
    ) -> Vec<Box<dyn SoftConstraint>> {
        vec![
            Box::new(CompactPlacement {
                style: "center".to_string(),
            }),
            Box::new(feasible_aspect_ratio(ctx.geometry)),
        ]
    }
}
