use anyhow::{bail, Context, Result};
use std::env;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Instant;

/// Common interface for external EDA subprocess runners.
pub trait ToolRunner {
    /// Tool name (e.g., `"align"`, `"magic"`, `"ngspice"`).
    fn tool_name(&self) -> &'static str;

    /// Binary or script name to check on `PATH`.
    fn executable(&self) -> &str;

    /// Returns `true` if the underlying tool executable is discoverable on `PATH`.
    fn is_available(&self) -> bool {
        command_on_path(self.executable())
    }
}

/// Metadata from a completed subprocess invocation.
#[derive(Debug, Clone)]
pub struct CommandOutput {
    pub status_code: Option<i32>,
    pub log_path: PathBuf,
    pub duration_ms: u128,
}

/// Manages per-iteration directory isolation (`<work_dir>/iter_<N>/`).
#[derive(Debug, Clone)]
pub struct IterationWorkspace {
    pub iteration: usize,
    pub root_dir: PathBuf,
    pub design_dir: PathBuf,
}

impl IterationWorkspace {
    /// Creates `<base_work_dir>/iter_<iteration>` and its `design/` staging folder.
    pub fn prepare(base_work_dir: &Path, iteration: usize) -> Result<Self> {
        let root_dir = base_work_dir.join(format!("iter_{iteration}"));
        let design_dir = root_dir.join("design");
        fs::create_dir_all(&design_dir).with_context(|| {
            format!(
                "Failed to create iteration workspace at {}",
                design_dir.display()
            )
        })?;
        Ok(Self {
            iteration,
            root_dir,
            design_dir,
        })
    }
}

/// Checks whether `bin` exists as an executable path or on `PATH`.
pub fn command_on_path(bin: &str) -> bool {
    let candidate = Path::new(bin);
    if candidate.components().count() > 1 {
        return candidate.is_file();
    }
    env::var_os("PATH").is_some_and(|paths| {
        env::split_paths(&paths).any(|dir| {
            let full = dir.join(bin);
            full.is_file()
        })
    })
}

/// Executes `cmd`, optionally piping `stdin_file`, writes combined stdout/stderr to `log_path`,
/// and fails with the tail of the log if the process exits non-zero.
pub fn execute_logged(
    cmd: &mut Command,
    stdin_file: Option<&Path>,
    log_path: &Path,
) -> Result<CommandOutput> {
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)?;
    }

    if let Some(input_path) = stdin_file {
        let file = File::open(input_path).with_context(|| {
            format!(
                "Failed to open stdin file {} for subprocess",
                input_path.display()
            )
        })?;
        cmd.stdin(Stdio::from(file));
    } else {
        cmd.stdin(Stdio::null());
    }

    let start = Instant::now();
    let output = cmd
        .output()
        .with_context(|| format!("Failed to spawn subprocess {:?}", cmd.get_program()))?;
    let duration_ms = start.elapsed().as_millis();

    let mut log_file = File::create(log_path)
        .with_context(|| format!("Failed to create log file {}", log_path.display()))?;
    log_file.write_all(&output.stdout)?;
    if !output.stderr.is_empty() {
        log_file.write_all(b"\n--- STDERR ---\n")?;
        log_file.write_all(&output.stderr)?;
    }

    if !output.status.success() {
        let combined = String::from_utf8_lossy(&output.stdout);
        let err_text = String::from_utf8_lossy(&output.stderr);
        let tail: Vec<&str> = combined
            .lines()
            .chain(err_text.lines())
            .rev()
            .take(15)
            .collect();
        let tail_joined = tail.into_iter().rev().collect::<Vec<_>>().join("\n");
        bail!(
            "Command {:?} exited with status {:?} (log: {}):\n{}",
            cmd.get_program(),
            output.status.code(),
            log_path.display(),
            tail_joined
        );
    }

    Ok(CommandOutput {
        status_code: output.status.code(),
        log_path: log_path.to_path_buf(),
        duration_ms,
    })
}
