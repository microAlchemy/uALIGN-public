use anyhow::{bail, Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Default)]
pub struct LayoutGeometry {
    pub width_um: f64,
    pub height_um: f64,
    pub active_area_um2: f64,
    pub total_wirelength_um: f64,
    pub total_vias: usize,
}

#[derive(Debug, Deserialize)]
struct PnrLayoutFile {
    bbox: [f64; 4],
    #[serde(default)]
    terminals: Vec<PnrTerminal>,
}

#[derive(Debug, Deserialize)]
struct PnrTerminal {
    layer: String,
    #[serde(rename = "netType", default)]
    net_type: Option<String>,
    rect: [f64; 4],
}

/// Parses an ALIGN `3_pnr/<CIRCUIT>_0.json` file into [`LayoutGeometry`].
pub fn parse_pnr_layout(json_path: &Path) -> Result<LayoutGeometry> {
    let content = fs::read_to_string(json_path)
        .with_context(|| format!("Failed to read PnR JSON file {}", json_path.display()))?;
    parse_pnr_layout_str(&content)
}

/// Parses an ALIGN PnR JSON string into [`LayoutGeometry`].
/// Coordinates in ALIGN's PnR JSON are in nanometers (`1e-3` um).
pub fn parse_pnr_layout_str(content: &str) -> Result<LayoutGeometry> {
    let parsed: PnrLayoutFile =
        serde_json::from_str(content).context("Failed to deserialize ALIGN PnR JSON")?;

    let [bx0, by0, bx1, by1] = parsed.bbox;
    let width_um = ((bx1 - bx0).abs()) / 1000.0;
    let height_um = ((by1 - by0).abs()) / 1000.0;
    if width_um <= 0.0 || height_um <= 0.0 {
        bail!("Invalid non-positive bbox {:?} in PnR JSON", parsed.bbox);
    }

    let mut active_diffusion_area_um2 = 0.0;
    let mut subblock_boundary_area_um2 = 0.0;
    let mut implant_area_um2 = 0.0;
    let mut total_wirelength_um = 0.0;
    let mut total_vias = 0usize;

    let bbox_area_um2 = width_um * height_um;

    for term in &parsed.terminals {
        if term.net_type.as_deref() == Some("blockage") {
            continue;
        }

        let [x0, y0, x1, y1] = term.rect;
        let w_um = (x1 - x0).abs() / 1000.0;
        let h_um = (y1 - y0).abs() / 1000.0;
        let area_um2 = w_um * h_um;
        let layer = term.layer.as_str();

        if is_metal_layer(layer) {
            total_wirelength_um += w_um.max(h_um);
        } else if is_via_layer(layer) {
            total_vias += 1;
        } else if layer.eq_ignore_ascii_case("Active") {
            active_diffusion_area_um2 += area_um2;
        } else if layer.eq_ignore_ascii_case("Boundary") && (area_um2 - bbox_area_um2).abs() > 1e-6
        {
            subblock_boundary_area_um2 += area_um2;
        } else if layer.eq_ignore_ascii_case("Nselect") || layer.eq_ignore_ascii_case("Pselect") {
            implant_area_um2 += area_um2;
        }
    }

    let active_area_um2 = if active_diffusion_area_um2 > 0.0 {
        active_diffusion_area_um2
    } else if subblock_boundary_area_um2 > 0.0 {
        subblock_boundary_area_um2
    } else {
        implant_area_um2
    };

    Ok(LayoutGeometry {
        width_um,
        height_um,
        active_area_um2,
        total_wirelength_um,
        total_vias,
    })
}

fn is_metal_layer(layer: &str) -> bool {
    let bytes = layer.as_bytes();
    bytes.len() >= 2
        && (bytes[0] == b'M' || bytes[0] == b'm')
        && bytes[1..].iter().all(|b| b.is_ascii_digit())
}

fn is_via_layer(layer: &str) -> bool {
    let bytes = layer.as_bytes();
    bytes.len() >= 2
        && (bytes[0] == b'V' || bytes[0] == b'v')
        && bytes[1..].iter().all(|b| b.is_ascii_digit())
}
