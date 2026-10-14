use align_autotune::analysis::foms::FoMReport;
use align_autotune::config::{AutoTuneConfig, TargetFoMs};
use align_autotune::flow::run_autotune_loop;
use align_autotune::remediation::constraints::{
    AlignInOrder, AspectRatio, HardConstraint, SoftConstraint, SymmetricNets, UserConstraint,
};
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

#[test]
fn test_constraint_trait_hierarchy() {
    let soft = SymmetricNets {
        direction: "V".to_string(),
        net1: "VIN".to_string(),
        net2: "VIP".to_string(),
        pins1: None,
        pins2: None,
    };
    assert_eq!(soft.constraint_name(), "SymmetricNets");
    assert_eq!(soft.to_json()["constraint"], "SymmetricNets");

    let hard = AspectRatio {
        ratio_low: 0.5,
        ratio_high: 2.0,
        weight: Some(1),
    };
    assert!(hard.validate().is_ok());
    assert_eq!(hard.to_json()["constraint"], "AspectRatio");

    let user = AlignInOrder {
        instances: vec!["MN0".to_string(), "MN1".to_string()],
        line: "bottom".to_string(),
        direction: None,
        abut: true,
    };
    assert!(user.validate().is_ok());
    let expanded = user.expand();
    assert_eq!(expanded.len(), 3);
    assert_eq!(expanded[0].constraint_name(), "Align");
    assert_eq!(expanded[1].constraint_name(), "Order");
    assert_eq!(expanded[2].constraint_name(), "DoNotIdentify");
}
