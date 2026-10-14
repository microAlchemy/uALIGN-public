use anyhow::{Context, Result};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::runners::{execute_logged, ToolRunner};

#[derive(Debug, Clone)]
pub struct NgspiceRunner {
    pub executable: String,
    pub spice_model_dirs: Vec<PathBuf>,
}

impl Default for NgspiceRunner {
    fn default() -> Self {
        Self {
            executable: "ngspice".to_string(),
            spice_model_dirs: discover_sky130_spice_dirs(),
        }
    }
}

impl ToolRunner for NgspiceRunner {
    fn tool_name(&self) -> &'static str {
        "ngspice"
    }

    fn executable(&self) -> &str {
        &self.executable
    }
}

impl NgspiceRunner {
    /// Runs `ngspice -b` on `testbench_path` inside `work_dir` and returns the path to `ngspice.log`.
    pub fn run(&self, testbench_path: &Path, work_dir: &Path) -> Result<PathBuf> {
        fs::create_dir_all(work_dir)?;
        let tb_abs = fs::canonicalize(testbench_path)
            .with_context(|| format!("Testbench not found: {}", testbench_path.display()))?;
        let work_abs = fs::canonicalize(work_dir)?;

        self.ensure_spiceinit(&work_abs)?;

        let staged_tb = work_abs.join("tb.sp");
        if tb_abs != staged_tb {
            fs::copy(&tb_abs, &staged_tb)?;
        }

        let log_path = work_abs.join("ngspice.log");
        let mut cmd = Command::new(&self.executable);
        cmd.current_dir(&work_abs).arg("-b").arg("tb.sp");

        execute_logged(&mut cmd, None, &log_path)?;
        Ok(log_path)
    }

    /// Writes `<work_dir>/.spiceinit` enabling `hsa` compatibility mode and configuring `sourcepath`
    /// so testbenches can `.include` official Sky130 model files without hardcoded machine paths.
    pub fn ensure_spiceinit(&self, work_dir: &Path) -> Result<PathBuf> {
        let spiceinit_path = work_dir.join(".spiceinit");
        if !spiceinit_path.exists() {
            let mut search_dirs = vec![".".to_string()];
            for dir in &self.spice_model_dirs {
                if dir.is_dir() {
                    search_dirs.push(dir.display().to_string());
                }
            }
            let content = format!(
                "set ngbehavior=hsa\nset ng_nomodcheck\nset sourcepath = ( {} )\n",
                search_dirs.join(" ")
            );
            fs::write(&spiceinit_path, content).with_context(|| {
                format!("Failed to write .spiceinit at {}", spiceinit_path.display())
            })?;
        }
        Ok(spiceinit_path)
    }
}

/// Discovers installed Sky130 SPICE model directories from `SKY130_PDK_ROOT`, `PDK_ROOT`,
/// `~/.ciel/sky130A`, or `~/.volare/sky130A`.
pub fn discover_sky130_spice_dirs() -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();

    if let Some(root) = env::var_os("SKY130_PDK_ROOT") {
        roots.push(PathBuf::from(root));
    }
    if let Some(pdk_root) = env::var_os("PDK_ROOT") {
        let base = PathBuf::from(pdk_root);
        roots.push(base.join("sky130A"));
        roots.push(base);
    }
    if let Some(home) = env::var_os("HOME") {
        let home_dir = PathBuf::from(home);
        roots.push(home_dir.join(".ciel/sky130A"));
        roots.push(home_dir.join(".volare/sky130A"));
    }

    let mut dirs = Vec::new();
    for root in roots {
        let pr_spice = root.join("libs.ref/sky130_fd_pr/spice");
        if pr_spice.is_dir() && !dirs.contains(&pr_spice) {
            dirs.push(pr_spice);
        }
        let tech_ngspice = root.join("libs.tech/ngspice");
        if tech_ngspice.is_dir() && !dirs.contains(&tech_ngspice) {
            dirs.push(tech_ngspice);
        }
    }
    dirs
}

/// Stages `testbench_src` as `<work_dir>/tb.sp` and copies `dut_netlist` as `<work_dir>/extracted.spice`
/// so each simulation directory is a self-contained snapshot compatible with `.include extracted.spice`.
pub fn prepare_dut_testbench(
    testbench_src: &Path,
    dut_netlist: &Path,
    work_dir: &Path,
) -> Result<PathBuf> {
    fs::create_dir_all(work_dir)?;
    let staged_tb = work_dir.join("tb.sp");
    fs::copy(testbench_src, &staged_tb).with_context(|| {
        format!(
            "Failed to copy testbench {} to {}",
            testbench_src.display(),
            staged_tb.display()
        )
    })?;

    let staged_dut = work_dir.join("extracted.spice");
    let dut_abs = fs::canonicalize(dut_netlist)
        .with_context(|| format!("DUT netlist not found: {}", dut_netlist.display()))?;
    let staged_dut_abs = fs::canonicalize(&staged_dut).ok();

    if staged_dut_abs.as_ref() != Some(&dut_abs) {
        fs::copy(&dut_abs, &staged_dut).with_context(|| {
            format!(
                "Failed to copy DUT netlist {} to {}",
                dut_abs.display(),
                staged_dut.display()
            )
        })?;
    }

    Ok(staged_tb)
}

pub fn run_simulation(testbench_path: &Path, work_dir: &Path) -> Result<PathBuf> {
    NgspiceRunner::default().run(testbench_path, work_dir)
}
