use anyhow::Result;
use std::path::Path;

#[derive(Debug, Default)]
pub struct LayoutGeometry {
    pub width_um: f64,
    pub height_um: f64,
    pub active_area_um2: f64,
    pub total_wirelength_um: f64,
    pub total_vias: usize,
}

pub fn parse_pnr_layout(_json_path: &Path) -> Result<LayoutGeometry> {
    // Barebones stub: parses 3_pnr/<CKT>_0.json geometry
    todo!("Parse ALIGN PnR JSON geometry")
}
