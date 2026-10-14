//! `align-autotune`: Automated closed-loop layout tuning wrapper for ALIGN, Magic PEX, and ngspice.

pub mod analysis;
pub mod config;
pub mod flow;
pub mod parsers;
pub mod remediation;
pub mod runners;

// Top-level re-exports for convenient library consumers and integration tests
pub use analysis::{
    calculate_differential_imbalance, evaluate_foms, find_net, rank_net_admittances,
    total_net_capacitance, AnalysisInputs, FoMReport, NetAdmittance,
};
pub use config::{default_reference_nets, AutoTuneConfig, TargetFoMs, DEFAULT_REFERENCE_NETS};
pub use flow::{discover_diff_pairs, discover_testbench, run_autotune_loop, AutoTuneSummary};
pub use parsers::{
    parse_ngspice_log, parse_ngspice_log_str, parse_pex_spice, parse_pex_spice_str,
    parse_pex_spice_str_with_refs, parse_pex_spice_with_refs, parse_pnr_layout,
    parse_pnr_layout_str, parse_spice_value, ExtractedCircuit, LayoutGeometry, PexNet,
    SimMeasurements,
};
pub use remediation::{
    constraints::{
        parse_constraints_json, Align, AlignInOrder, AspectRatio, Boundary, CompactPlacement,
        DoNotIdentify, Floorplan, GroundPorts, HardConstraint, NetConst, NetPriority, Order,
        PlaceCloser, PowerPorts, RawConstraint, SoftConstraint, SymmetricBlocks, SymmetricNets,
        UserConstraint,
    },
    diagnose_and_remediate, DiagnosisContext,
};
pub use runners::{
    discover_sky130_spice_dirs, execute_logged, extract_subckt_ports, find_pnr_json, find_top_gds,
    normalize_top_subckt_header, prepare_dut_testbench, stage_design_inputs, AlignResult,
    AlignRunner, CommandOutput, IterationWorkspace, MagicRunner, NgspiceRunner, ToolRunner,
};
