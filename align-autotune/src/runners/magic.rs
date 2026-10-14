use anyhow::{bail, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::runners::{execute_logged, ToolRunner};

#[derive(Debug, Clone)]
pub struct MagicRunner {
    pub executable: String,
}

impl Default for MagicRunner {
    fn default() -> Self {
        Self {
            executable: "magic".to_string(),
        }
    }
}

impl ToolRunner for MagicRunner {
    fn tool_name(&self) -> &'static str {
        "magic"
    }

    fn executable(&self) -> &str {
        &self.executable
    }
}

impl MagicRunner {
    /// Runs headless Magic `ext2spice` on `gds_path` and normalizes the top-level `.subckt` header in `extracted.spice`.
    pub fn run(
        &self,
        circuit: &str,
        gds_path: &Path,
        tech_dir: &Path,
        work_dir: &Path,
        expected_ports: &[String],
    ) -> Result<PathBuf> {
        fs::create_dir_all(work_dir)?;
        let gds_abs = fs::canonicalize(gds_path)
            .with_context(|| format!("GDS file not found: {}", gds_path.display()))?;
        let tech_dir_abs = fs::canonicalize(tech_dir)
            .with_context(|| format!("Magic tech_dir not found: {}", tech_dir.display()))?;
        let work_abs = fs::canonicalize(work_dir)?;

        let tech_file = find_file_with_ext(&tech_dir_abs, ".tech")?;
        let tcl_script = tech_dir_abs.join("ext2spice.tcl");
        if !tcl_script.is_file() {
            bail!(
                "Magic extraction script not found at {}",
                tcl_script.display()
            );
        }

        let mut cmd = Command::new(&self.executable);
        cmd.current_dir(&work_abs)
            .arg("-dnull")
            .arg("-noconsole")
            .arg("-T")
            .arg(&tech_file)
            .env("INPUT_GDS", &gds_abs)
            .env("OUTPUT_DIR", &work_abs)
            .env("CIRCUIT", circuit);

        let macports_tk = Path::new("/opt/local/lib/tk8-x11/tk8.6");
        if macports_tk.is_dir() && std::env::var_os("TK_LIBRARY").is_none() {
            cmd.env("TK_LIBRARY", macports_tk);
        }

        let log_path = work_abs.join("magic.log");
        execute_logged(&mut cmd, Some(&tcl_script), &log_path)?;

        let extracted_path = work_abs.join("extracted.spice");
        if !extracted_path.is_file() {
            bail!(
                "Magic completed but {} was not created (see {})",
                extracted_path.display(),
                log_path.display()
            );
        }

        let raw = fs::read_to_string(&extracted_path)?;
        let normalized = normalize_top_subckt_header(&raw, circuit, expected_ports);
        fs::write(&extracted_path, normalized)?;

        Ok(extracted_path)
    }
}

/// Extracts the ordered port list from `.subckt <circuit> <port1> <port2> ...` in a schematic SPICE file.
pub fn extract_subckt_ports(spice_text: &str, circuit: &str) -> Vec<String> {
    for line in spice_text.lines() {
        let trimmed = line.trim();
        if trimmed.to_ascii_lowercase().starts_with(".subckt ") {
            let tokens: Vec<&str> = trimmed.split_whitespace().collect();
            if tokens.len() >= 2 && tokens[1].eq_ignore_ascii_case(circuit) {
                return tokens[2..]
                    .iter()
                    .take_while(|tok| !tok.contains('='))
                    .map(|s| (*s).to_string())
                    .collect();
            }
        }
    }
    Vec::new()
}

/// Normalizes the top-level `.subckt` header in Magic's `ext2spice` output without stripping any SPICE parameters or options:
/// - Matches the top-level `.subckt <circuit>` (or `<CIRCUIT>_<variant>`) and ensures its name is `<circuit>` and its port order matches `expected_ports` when provided.
/// - Leaves any child primitive `.subckt` blocks untouched.
/// - Wraps flat decks without a top-level `.subckt` in `.subckt <circuit> <expected_ports> ... .ends <circuit>`.
pub fn normalize_top_subckt_header(
    raw_spice: &str,
    circuit: &str,
    expected_ports: &[String],
) -> String {
    let mut output_lines: Vec<String> = Vec::new();
    let mut in_top_subckt = false;
    let mut found_top_subckt = false;
    let variant_prefix = format!("{}_", circuit.to_ascii_uppercase());

    for line in raw_spice.lines() {
        let trimmed = line.trim();
        let lower = trimmed.to_ascii_lowercase();

        if lower.starts_with(".subckt ") {
            let tokens: Vec<&str> = trimmed.split_whitespace().collect();
            if tokens.len() >= 2 {
                let orig_name = tokens[1];
                let is_top = orig_name.eq_ignore_ascii_case(circuit)
                    || orig_name.to_ascii_uppercase().starts_with(&variant_prefix);
                if is_top {
                    in_top_subckt = true;
                    found_top_subckt = true;
                    let ports_str = if !expected_ports.is_empty() {
                        format!(" {}", expected_ports.join(" "))
                    } else if tokens.len() > 2 {
                        format!(" {}", tokens[2..].join(" "))
                    } else {
                        String::new()
                    };
                    output_lines.push(format!(".subckt {circuit}{ports_str}"));
                    continue;
                }
            }
        }

        if lower.starts_with(".ends") && in_top_subckt {
            in_top_subckt = false;
            output_lines.push(format!(".ends {circuit}"));
            continue;
        }

        output_lines.push(line.to_string());
    }

    if found_top_subckt {
        let mut out = output_lines.join("\n");
        out.push('\n');
        return out;
    }

    let ports_str = if expected_ports.is_empty() {
        String::new()
    } else {
        format!(" {}", expected_ports.join(" "))
    };

    let mut wrapped = Vec::with_capacity(output_lines.len() + 2);
    wrapped.push(format!(".subckt {circuit}{ports_str}"));
    for line in output_lines {
        if !line.trim().eq_ignore_ascii_case(".end") {
            wrapped.push(line);
        }
    }
    wrapped.push(format!(".ends {circuit}"));

    let mut out = wrapped.join("\n");
    out.push('\n');
    out
}

fn find_file_with_ext(dir: &Path, ext: &str) -> Result<PathBuf> {
    for entry in fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_file() {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if name.ends_with(ext) {
                    return Ok(path);
                }
            }
        }
    }
    bail!("No *{ext} file found in {}", dir.display())
}

pub fn run_pex(
    circuit: &str,
    gds_path: &Path,
    tech_dir: &Path,
    work_dir: &Path,
    expected_ports: &[String],
) -> Result<PathBuf> {
    MagicRunner::default().run(circuit, gds_path, tech_dir, work_dir, expected_ports)
}
