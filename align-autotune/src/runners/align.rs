use anyhow::Result;
use std::path::{Path, PathBuf};

pub struct AlignResult {
    pub gds_path: PathBuf,
    pub pnr_json_path: PathBuf,
}

pub fn run_align(
    _circuit: &str,
    _netlist: &Path,
    _pdk_path: &Path,
    _work_dir: &Path,
) -> Result<AlignResult> {
    // Barebones stub
    todo!("Invoke ALIGN schematic2layout subprocess")
}
