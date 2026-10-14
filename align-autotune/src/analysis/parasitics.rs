use crate::parsers::spice::{ExtractedCircuit, PexNet};

#[derive(Debug, Clone, PartialEq)]
pub struct NetAdmittance {
    pub net_name: String,
    pub capacitance: f64,
}

/// Total parasitic capacitance on a net (ground capacitance + all inter-net coupling capacitances).
pub fn total_net_capacitance(net: &PexNet) -> f64 {
    let coupling_sum: f64 = net.coupling_caps.values().sum();
    net.total_c_ground + coupling_sum
}

pub fn find_net<'a>(circuit: &'a ExtractedCircuit, target: &str) -> Option<&'a PexNet> {
    circuit.nets.get(target).or_else(|| {
        circuit
            .nets
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(target))
            .map(|(_, v)| v)
    })
}

/// Computes `|Cp - Cn| / max(Cp, Cn)` across total net capacitance (ground + coupling),
/// using case-insensitive net name matching.
pub fn calculate_differential_imbalance(
    circuit: &ExtractedCircuit,
    net_p: &str,
    net_n: &str,
) -> Option<f64> {
    let cp = total_net_capacitance(find_net(circuit, net_p)?);
    let cn = total_net_capacitance(find_net(circuit, net_n)?);
    let c_max = cp.max(cn);
    if c_max > 1e-18 {
        Some((cp - cn).abs() / c_max)
    } else {
        Some(0.0)
    }
}

/// Returns all extracted nets ranked from highest to lowest total parasitic capacitance.
pub fn rank_net_admittances(circuit: &ExtractedCircuit) -> Vec<NetAdmittance> {
    let mut list: Vec<NetAdmittance> = circuit
        .nets
        .values()
        .map(|net| NetAdmittance {
            net_name: net.name.clone(),
            capacitance: total_net_capacitance(net),
        })
        .collect();

    list.sort_by(|a, b| {
        b.capacitance
            .partial_cmp(&a.capacitance)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.net_name.cmp(&b.net_name))
    });
    list
}
