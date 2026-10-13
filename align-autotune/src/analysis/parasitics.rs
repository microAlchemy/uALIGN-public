use crate::parsers::spice::ExtractedCircuit;

pub struct NetAdmittance {
    pub net_name: String,
    pub capacitance: f64,
}

pub fn calculate_differential_imbalance(
    circuit: &ExtractedCircuit,
    net_p: &str,
    net_n: &str,
) -> Option<f64> {
    let cp = circuit.nets.get(net_p)?.total_c_ground;
    let cn = circuit.nets.get(net_n)?.total_c_ground;
    let c_max = cp.max(cn);
    if c_max > 1e-18 {
        Some((cp - cn).abs() / c_max)
    } else {
        Some(0.0)
    }
}
