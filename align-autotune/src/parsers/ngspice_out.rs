use anyhow::{Context, Result};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Default)]
pub struct SimMeasurements {
    pub measures: HashMap<String, f64>,
}

/// Parses `.measure` results (and fallback `.print ac` rows) from an `ngspice` batch log file.
pub fn parse_ngspice_log(log_path: &Path) -> Result<SimMeasurements> {
    let content = fs::read_to_string(log_path)
        .with_context(|| format!("Failed to read ngspice log {}", log_path.display()))?;
    parse_ngspice_log_str(&content)
}

/// Parses `.measure` outputs from an `ngspice` log string.
pub fn parse_ngspice_log_str(content: &str) -> Result<SimMeasurements> {
    let mut measures = HashMap::new();

    for line in content.lines() {
        if let Some((name, val)) = parse_measure_line(line) {
            measures.insert(name, val);
        }
    }

    // Fallback: if AC print table rows (`index freq real, imag`) exist and ugbw/gain_lin were not measured
    if !measures.contains_key("ugbw") && !measures.contains_key("ugbw_mhz") {
        let (gain_opt, ugbw_opt) = parse_ac_print_table(content);
        if let Some(gain) = gain_opt {
            measures.entry("gain_lin".to_string()).or_insert(gain);
        }
        if let Some(ugbw_hz) = ugbw_opt {
            measures.insert("ugbw".to_string(), ugbw_hz);
            measures.insert("ugbw_mhz".to_string(), ugbw_hz * 1e-6);
        }
    }

    Ok(SimMeasurements { measures })
}

/// Matches lines of the form `  <ident> = <float> [at= ... | targ= ...]`
/// while ignoring prose lines like `Total elapsed time (seconds) = 0.127`.
fn parse_measure_line(line: &str) -> Option<(String, f64)> {
    let trimmed = line.trim();
    let (lhs, rhs) = trimmed.split_once('=')?;
    let name = lhs.trim();

    if name.is_empty()
        || !name
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
    {
        return None;
    }

    let val_tok = rhs.split_whitespace().next()?;
    let val = val_tok.parse::<f64>().ok()?;
    if !val.is_finite() {
        return None;
    }

    Some((name.to_ascii_lowercase(), val))
}

fn parse_ac_print_table(content: &str) -> (Option<f64>, Option<f64>) {
    let mut rows: Vec<(f64, f64)> = Vec::new();

    for line in content.lines() {
        let tokens: Vec<&str> = line.split_whitespace().collect();
        if tokens.len() < 3 {
            continue;
        }
        if tokens[0].parse::<usize>().is_err() {
            continue;
        }
        let Ok(freq) = tokens[1].parse::<f64>() else {
            continue;
        };

        if let Some(comma_stripped) = tokens[2].strip_suffix(',') {
            if tokens.len() >= 4 {
                if let (Ok(re), Ok(im)) = (comma_stripped.parse::<f64>(), tokens[3].parse::<f64>())
                {
                    rows.push((freq, re.hypot(im)));
                }
            }
        }
    }

    if rows.len() < 2 {
        return (None, None);
    }

    let max_mag = rows.iter().map(|&(_, m)| m).fold(0.0_f64, f64::max);
    let gain_lin = (max_mag > 0.0).then_some(max_mag);

    let mut ugbw_hz = None;
    if max_mag > 1.0 {
        for pair in rows.windows(2) {
            let (f1, m1) = pair[0];
            let (f2, m2) = pair[1];
            if m1 >= 1.0 && m2 <= 1.0 && (m2 - m1).abs() > f64::EPSILON && f1 > 0.0 && f2 > 0.0 {
                let t = (1.0 - m1) / (m2 - m1);
                ugbw_hz = Some(f1 * (f2 / f1).powf(t));
                break;
            }
        }
    }

    (gain_lin, ugbw_hz)
}
