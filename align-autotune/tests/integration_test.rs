use align_autotune::analysis::foms::FoMReport;
use align_autotune::config::{AutoTuneConfig, TargetFoMs};
use align_autotune::flow::run_autotune_loop;
use std::path::PathBuf;

#[test]
fn test_barebones_flow() {
    let config = AutoTuneConfig {
        circuit: "test_circuit".to_string(),
        netlist_path: PathBuf::from("dummy.sp"),
        pdk_path: PathBuf::from("pdks/FinFET14nm_Mock_PDK"),
        work_dir: PathBuf::from("/tmp/test_autotune"),
        max_iterations: 1,
        targets: TargetFoMs::default(),
    };

    let result = run_autotune_loop(&config);
    assert!(result.is_ok());
}

#[test]
fn test_fom_satisfactory() {
    let report = FoMReport {
        diff_cap_imbalance_ratio: Some(0.015),
        bandwidth_retention_ratio: Some(0.90),
        area_efficiency: Some(0.45),
    };
    assert!(report.is_satisfactory(0.02, 0.85));
}
