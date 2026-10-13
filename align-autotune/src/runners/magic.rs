use anyhow::Result;
use std::path::{Path, PathBuf};

pub fn run_pex(
    _circuit: &str,
    _gds_path: &Path,
    _tech_dir: &Path,
    _work_dir: &Path,
) -> Result<PathBuf> {
    // Barebones stub: invokes Magic ext2spice and sanitizes output to extracted.spice
    todo!("Invoke Magic ext2spice extraction and wrap into subcircuit")
}
