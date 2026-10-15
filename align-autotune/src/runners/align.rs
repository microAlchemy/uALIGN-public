use anyhow::{bail, Context, Result};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::remediation::constraints::SoftConstraint;
use crate::runners::{command_on_path, execute_logged, ToolRunner};

#[derive(Debug, Clone)]
pub struct AlignResult {
    pub gds_path: PathBuf,
    pub pnr_json_path: PathBuf,
    pub log_path: PathBuf,
    pub runtime_ms: u128,
}

#[derive(Debug, Clone)]
pub struct AlignRunner {
    pub executable: String,
    pub extra_lib_dirs: Vec<PathBuf>,
}

impl Default for AlignRunner {
    fn default() -> Self {
        Self {
            executable: discover_align_executable(),
            extra_lib_dirs: discover_align_lib_dirs(),
        }
    }
}

impl ToolRunner for AlignRunner {
    fn tool_name(&self) -> &'static str {
        "align"
    }

    fn executable(&self) -> &str {
        &self.executable
    }
}

impl AlignRunner {
    /// Invokes ALIGN inside `work_dir` on a design directory containing `<circuit>.sp` (and optional `<circuit>.const.json`).
    pub fn run(
        &self,
        circuit: &str,
        netlist: &Path,
        pdk_path: &Path,
        work_dir: &Path,
    ) -> Result<AlignResult> {
        fs::create_dir_all(work_dir)
            .with_context(|| format!("Failed to create ALIGN work_dir {}", work_dir.display()))?;

        let netlist_abs = fs::canonicalize(netlist)
            .with_context(|| format!("Netlist path not found: {}", netlist.display()))?;
        let pdk_abs = fs::canonicalize(pdk_path)
            .with_context(|| format!("PDK path not found: {}", pdk_path.display()))?;
        let work_abs = fs::canonicalize(work_dir)?;

        let design_dir = netlist_abs
            .parent()
            .context("Netlist file has no parent directory")?;

        let mut cmd = if self.executable.ends_with(".py")
            && Path::new(&self.executable).components().count() > 1
        {
            let py = discover_align_python();
            let mut c = Command::new(py);
            c.arg(&self.executable);
            c
        } else {
            Command::new(&self.executable)
        };
        cmd.current_dir(&work_abs)
            .arg(design_dir)
            .arg("-f")
            .arg(&netlist_abs)
            .arg("-s")
            .arg(circuit)
            .arg("-p")
            .arg(&pdk_abs);

        if let Some(repo_root) = discover_repo_root() {
            if env::var_os("PYTHONPATH").is_none() {
                cmd.env("PYTHONPATH", &repo_root);
            }
        }

        if !self.extra_lib_dirs.is_empty() {
            let joined = env::join_paths(&self.extra_lib_dirs)?;
            cmd.env("DYLD_LIBRARY_PATH", &joined)
                .env("LD_LIBRARY_PATH", &joined);
        }

        let log_path = work_abs.join("align.log");
        let output = execute_logged(&mut cmd, None, &log_path)?;

        let gds_path = find_top_gds(&work_abs, circuit)?;
        let pnr_json_path = find_pnr_json(&work_abs, circuit)?;

        Ok(AlignResult {
            gds_path,
            pnr_json_path,
            log_path: output.log_path,
            runtime_ms: output.duration_ms,
        })
    }
}

/// Stages `<circuit>.sp` and serializes `constraints` to `<circuit>.const.json` inside `design_dir`.
/// Returns the staged netlist path (`<design_dir>/<circuit>.sp`).
pub fn stage_design_inputs(
    circuit: &str,
    source_netlist: &Path,
    constraints: &[Box<dyn SoftConstraint>],
    design_dir: &Path,
) -> Result<PathBuf> {
    fs::create_dir_all(design_dir)?;
    let staged_netlist = design_dir.join(format!("{circuit}.sp"));
    fs::copy(source_netlist, &staged_netlist).with_context(|| {
        format!(
            "Failed to copy netlist {} to {}",
            source_netlist.display(),
            staged_netlist.display()
        )
    })?;

    let const_path = design_dir.join(format!("{circuit}.const.json"));
    if !constraints.is_empty() {
        let json_list: Vec<serde_json::Value> = constraints.iter().map(|c| c.to_json()).collect();
        let serialized = serde_json::to_string_pretty(&json_list)?;
        fs::write(&const_path, serialized)?;
    } else if let Some(src_dir) = source_netlist.parent() {
        let existing_const = src_dir.join(format!("{circuit}.const.json"));
        if existing_const.is_file() {
            fs::copy(&existing_const, &const_path)?;
        }
    }

    Ok(staged_netlist)
}

/// Locates the top-level GDS file in `work_dir` (non-recursive, preferring `<CIRCUIT>_0.gds` over `.python.gds`).
pub fn find_top_gds(work_dir: &Path, circuit: &str) -> Result<PathBuf> {
    let expected = work_dir.join(format!("{}_0.gds", circuit.to_uppercase()));
    if expected.is_file() {
        return Ok(expected);
    }

    let mut fallback_python_gds = None;
    for entry in fs::read_dir(work_dir)? {
        let path = entry?.path();
        if !path.is_file() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if name.ends_with(".python.gds") {
            fallback_python_gds = Some(path);
        } else if name.ends_with(".gds") {
            return Ok(path);
        }
    }

    if let Some(py_gds) = fallback_python_gds {
        return Ok(py_gds);
    }

    bail!("No GDS file found in top-level of {}", work_dir.display())
}

/// Locates `3_pnr/<CIRCUIT>_0.json` inside `work_dir`.
pub fn find_pnr_json(work_dir: &Path, circuit: &str) -> Result<PathBuf> {
    let pnr_dir = work_dir.join("3_pnr");
    let expected = pnr_dir.join(format!("{}_0.json", circuit.to_uppercase()));
    if expected.is_file() {
        return Ok(expected);
    }

    if pnr_dir.is_dir() {
        for entry in fs::read_dir(&pnr_dir)? {
            let path = entry?.path();
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if name.ends_with("_0.json")
                    && !name.ends_with(".elford.json")
                    && !name.ends_with(".verilog.json")
                {
                    return Ok(path);
                }
            }
        }
    }

    bail!(
        "No PnR JSON (<CIRCUIT>_0.json) found under {}/3_pnr",
        work_dir.display()
    )
}

pub fn run_align(
    circuit: &str,
    netlist: &Path,
    pdk_path: &Path,
    work_dir: &Path,
) -> Result<AlignResult> {
    AlignRunner::default().run(circuit, netlist, pdk_path, work_dir)
}

fn discover_repo_root() -> Option<PathBuf> {
    let cwd = env::current_dir().ok()?;
    for ancestor in cwd.ancestors() {
        if ancestor.join("bin/schematic2layout.py").is_file() {
            return Some(ancestor.to_path_buf());
        }
    }
    None
}

fn discover_align_executable() -> String {
    if command_on_path("schematic2layout.py") {
        return "schematic2layout.py".to_string();
    }
    if let Some(root) = discover_repo_root() {
        let script = root.join("bin/schematic2layout.py");
        if script.is_file() {
            return script.display().to_string();
        }
    }
    "schematic2layout.py".to_string()
}

fn discover_align_python() -> String {
    if let Ok(py) = env::var("ALIGN_PYTHON") {
        return py;
    }
    if let Ok(venv) = env::var("VIRTUAL_ENV") {
        let py = Path::new(&venv).join("bin/python3");
        if py.is_file() {
            return py.display().to_string();
        }
    }
    if let Some(root) = discover_repo_root() {
        if let Some(parent) = root.parent() {
            let sibling_venv = parent.join("general/bin/python3");
            if sibling_venv.is_file() {
                return sibling_venv.display().to_string();
            }
        }
    }
    "python3".to_string()
}

fn discover_align_lib_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    let Some(root) = discover_repo_root() else {
        return dirs;
    };
    let skbuild = root.join("_skbuild");
    if let Ok(entries) = fs::read_dir(&skbuild) {
        for entry in entries.flatten() {
            let thirdparty = entry.path().join("cmake-install/align/thirdparty");
            if thirdparty.is_dir() {
                dirs.push(thirdparty);
            }
        }
    }
    dirs.push(root);
    dirs
}
