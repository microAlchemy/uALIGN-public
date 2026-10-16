pub mod foms;
pub mod impls;
pub mod parasitics;

pub use foms::{evaluate_foms, AnalysisInputs, FigureOfMerit, FoM, FoMReport, LIST_OF_FOMS};
pub use impls::{
    AreaEfficiency, BandwidthRetentionRatio, BlockPackingEfficiency, DiffCapImbalanceRatio,
};
pub use parasitics::{
    calculate_differential_imbalance, find_net, net_capacitance_excluding, rank_net_admittances,
    total_net_capacitance, NetAdmittance,
};
