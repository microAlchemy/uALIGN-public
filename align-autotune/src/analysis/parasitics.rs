use crate::parsers::spice::{ExtractedCircuit, PexNet};

#[derive(Debug, Clone, PartialEq)]
pub struct NetAdmittance {
    pub net_name: String,
    pub capacitance: f64,
}

/// Total parasitic capacitance on a net (ground capacitance + all inter-net coupling capacitances),
/// summed in deterministic key order.
pub fn total_net_capacitance(net: &PexNet) -> f64 {
    net_capacitance_excluding(net, None)
}

/// Total parasitic capacitance on `net`, optionally excluding mutual coupling to `excluded_peer`
/// (used for common-mode differential pair capacitance where $V_p = V_n$ produces zero current through $C_{pn}$).
pub fn net_capacitance_excluding(net: &PexNet, excluded_peer: Option<&str>) -> f64 {
    let mut keys: Vec<&String> = net
        .coupling_caps
        .keys()
        .filter(|k| !excluded_peer.is_some_and(|ex| k.eq_ignore_ascii_case(ex)))
        .collect();
    keys.sort();
    let coupling_sum: f64 = keys.into_iter().map(|k| net.coupling_caps[k]).sum();
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

/// Computes common-mode differential capacitance imbalance `|Cp - Cn| / max(Cp, Cn)`
/// (excluding the reciprocal mutual capacitor `C_pn` directly between `net_p` and `net_n`,
/// which carries zero current under common-mode excitation and would otherwise dilute `max(Cp, Cn)`).
pub fn calculate_differential_imbalance(
    circuit: &ExtractedCircuit,
    net_p: &str,
    net_n: &str,
) -> Option<f64> {
    let cp = net_capacitance_excluding(find_net(circuit, net_p)?, Some(net_n));
    let cn = net_capacitance_excluding(find_net(circuit, net_n)?, Some(net_p));
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
