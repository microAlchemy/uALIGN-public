use crate::config::default_reference_nets;
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Default)]
pub struct PexNet {
    pub name: String,
    pub total_c_ground: f64,
    pub coupling_caps: HashMap<String, f64>,
}

#[derive(Debug, Clone, Default)]
pub struct ExtractedCircuit {
    pub nets: HashMap<String, PexNet>,
}

/// Parses a Magic `extracted.spice` file into per-net ground and coupling capacitances (in Farads)
pub fn parse_pex_spice(spice_path: &Path) -> Result<ExtractedCircuit> {
    let defaults = default_reference_nets();
    parse_pex_spice_with_refs(spice_path, &defaults)
}

/// Parses a Magic `extracted.spice` file, overriding the default reference nets with `reference_nets`.
pub fn parse_pex_spice_with_refs(
    spice_path: &Path,
    reference_nets: &[String],
) -> Result<ExtractedCircuit> {
    let content = fs::read_to_string(spice_path)
        .with_context(|| format!("Failed to read PEX SPICE file {}", spice_path.display()))?;
    parse_pex_spice_str_with_refs(&content, reference_nets)
}

/// Parses SPICE text containing lumped RC parasitics into an [`ExtractedCircuit`]
pub fn parse_pex_spice_str(content: &str) -> Result<ExtractedCircuit> {
    let defaults = default_reference_nets();
    parse_pex_spice_str_with_refs(content, &defaults)
}

/// Parses SPICE text containing lumped RC parasitics, overriding the default reference nets with `reference_nets`.
pub fn parse_pex_spice_str_with_refs(
    content: &str,
    reference_nets: &[String],
) -> Result<ExtractedCircuit> {
    let logical_lines = join_continuation_lines(content);
    let mut nets: HashMap<String, PexNet> = HashMap::new();

    for raw_line in logical_lines {
        let line = strip_inline_comment(&raw_line).trim();
        if line.is_empty() || line.starts_with('*') || line.starts_with('.') {
            continue;
        }

        let first_char = line.chars().next().unwrap_or(' ');
        if !first_char.eq_ignore_ascii_case(&'c') {
            continue;
        }

        let tokens: Vec<&str> = line.split_whitespace().collect();
        if tokens.len() < 4 {
            continue;
        }

        let node1 = tokens[1];
        let node2 = tokens[2];
        if node1.eq_ignore_ascii_case(node2) {
            continue;
        }

        let cap_val = extract_cap_value(&tokens[3..]);
        let Some(cap_f) = cap_val else {
            continue;
        };

        // Ignore negative numerical noise from extraction halo subtraction
        if cap_f <= 0.0 || !cap_f.is_finite() {
            continue;
        }

        let n1_is_ref = is_reference_node(node1, reference_nets);
        let n2_is_ref = is_reference_node(node2, reference_nets);

        match (n1_is_ref, n2_is_ref) {
            (true, true) => {}
            (false, true) => {
                let entry = get_or_insert_net(&mut nets, node1);
                entry.total_c_ground += cap_f;
            }
            (true, false) => {
                let entry = get_or_insert_net(&mut nets, node2);
                entry.total_c_ground += cap_f;
            }
            (false, false) => {
                {
                    let entry1 = get_or_insert_net(&mut nets, node1);
                    *entry1.coupling_caps.entry(node2.to_string()).or_insert(0.0) += cap_f;
                }
                {
                    let entry2 = get_or_insert_net(&mut nets, node2);
                    *entry2.coupling_caps.entry(node1.to_string()).or_insert(0.0) += cap_f;
                }
            }
        }
    }

    Ok(ExtractedCircuit { nets })
}

fn get_or_insert_net<'a>(nets: &'a mut HashMap<String, PexNet>, name: &str) -> &'a mut PexNet {
    if !nets.contains_key(name) {
        nets.insert(
            name.to_string(),
            PexNet {
                name: name.to_string(),
                total_c_ground: 0.0,
                coupling_caps: HashMap::new(),
            },
        );
    }
    nets.get_mut(name).unwrap()
}

/// using the default power/ground reference net conventions (`0`, `sub`, `gnd`, `vss`, `vgnd`, `vsubs`, `vdd`, `vpwr`).
fn is_reference_node(node: &str, reference_nets: &[String]) -> bool {
    node.eq_ignore_ascii_case("0") || reference_nets.iter().any(|r| r.eq_ignore_ascii_case(node))
}

fn extract_cap_value(tokens: &[&str]) -> Option<f64> {
    for tok in tokens {
        let lower = tok.to_ascii_lowercase();
        if let Some(rest) = lower
            .strip_prefix("c=")
            .or_else(|| lower.strip_prefix("value="))
        {
            if let Some(v) = parse_spice_value(rest) {
                return Some(v);
            }
        }
    }
    parse_spice_value(tokens[0])
}

/// Parses a SPICE numeric literal with optional engineering suffix (`f`, `p`, `n`, `u`, `m`, `meg`, `k`, `g`, `t`).
pub fn parse_spice_value(raw: &str) -> Option<f64> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }

    if let Ok(v) = s.parse::<f64>() {
        return Some(v);
    }

    let lower = s.to_ascii_lowercase();
    let split_idx = lower
        .char_indices()
        .find(|&(i, c)| {
            if c.is_ascii_digit() || c == '.' {
                return false;
            }
            if c == '+' || c == '-' {
                if i == 0 {
                    return false;
                }
                let prev = lower.as_bytes()[i - 1];
                return prev != b'e';
            }
            if c == 'e' {
                return lower[i + 1..].chars().next().is_none_or(|next_c| {
                    !next_c.is_ascii_digit() && next_c != '+' && next_c != '-'
                });
            }
            true
        })
        .map(|(i, _)| i)?;

    let (num_part, unit_part) = lower.split_at(split_idx);
    let base: f64 = num_part.parse().ok()?;

    let mult = if unit_part.starts_with("meg") {
        1e6
    } else if unit_part.starts_with('f') {
        1e-15
    } else if unit_part.starts_with('p') {
        1e-12
    } else if unit_part.starts_with('n') {
        1e-9
    } else if unit_part.starts_with('u') {
        1e-6
    } else if unit_part.starts_with('m') {
        1e-3
    } else if unit_part.starts_with('k') {
        1e3
    } else if unit_part.starts_with('g') {
        1e9
    } else if unit_part.starts_with('t') {
        1e12
    } else {
        return None;
    };

    Some(base * mult)
}

fn join_continuation_lines(content: &str) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    for raw in content.lines() {
        let trimmed = raw.trim_start();
        if let Some(rest) = trimmed.strip_prefix('+') {
            if let Some(last) = lines.last_mut() {
                last.push(' ');
                last.push_str(rest.trim_start());
                continue;
            }
        }
        lines.push(raw.to_string());
    }
    lines
}

fn strip_inline_comment(line: &str) -> &str {
    let end = line.find(['$', ';']).unwrap_or(line.len());
    &line[..end]
}
