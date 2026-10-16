use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AutoTuneConfig {
    pub circuit: String,
    pub netlist_path: PathBuf,
    pub pdk_path: PathBuf,
    #[serde(default)]
    pub testbench_path: Option<PathBuf>,
    pub work_dir: PathBuf,
    pub max_iterations: usize,
    #[serde(default = "default_reference_nets")]
    pub reference_nets: Vec<String>,
}

pub const DEFAULT_REFERENCE_NETS: &[&str] =
    &["0", "sub", "gnd", "vss", "vgnd", "vsubs", "vdd", "vpwr"];

pub fn default_reference_nets() -> Vec<String> {
    DEFAULT_REFERENCE_NETS
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}
