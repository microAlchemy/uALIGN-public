use align_autotune::analysis::{evaluate_foms, rank_net_admittances, AnalysisInputs, FoMReport};
use align_autotune::config::{AutoTuneConfig, TargetFoMs};
use align_autotune::flow::run_autotune_loop;
use align_autotune::parsers::ngspice_out::SimMeasurements;
use align_autotune::parsers::pnr_json::LayoutGeometry;
use align_autotune::parsers::spice::{ExtractedCircuit, PexNet};
use align_autotune::remediation::constraints::{
    AlignInOrder, AspectRatio, HardConstraint, SoftConstraint, SymmetricNets, UserConstraint,
};
use align_autotune::runners::{
    extract_subckt_ports, find_top_gds, normalize_top_subckt_header, stage_design_inputs,
    IterationWorkspace,
};
use std::collections::HashMap;
use std::fs;
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
    let targets = TargetFoMs::default();
    assert!(!FoMReport::default().meets_targets(&targets));

    let report = FoMReport {
        diff_cap_imbalance_ratio: Some(0.015),
        bandwidth_retention_ratio: Some(0.90),
        area_efficiency: Some(0.45),
    };
    assert!(report.meets_targets(&targets));

    let worse_report = FoMReport {
        diff_cap_imbalance_ratio: Some(0.05),
        bandwidth_retention_ratio: Some(0.80),
        area_efficiency: Some(0.50),
    };
    assert!(report.is_better_than(&worse_report, &targets));
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

#[test]
fn test_evaluate_foms() {
    let geometry = LayoutGeometry {
        width_um: 10.0,
        height_um: 10.0,
        active_area_um2: 42.0,
        total_wirelength_um: 120.0,
        total_vias: 16,
    };

    let mut nets = HashMap::new();
    nets.insert(
        "VIP".to_string(),
        PexNet {
            name: "VIP".to_string(),
            total_c_ground: 10.0e-15,
            coupling_caps: HashMap::new(),
        },
    );
    nets.insert(
        "VIN".to_string(),
        PexNet {
            name: "VIN".to_string(),
            total_c_ground: 9.9e-15,
            coupling_caps: HashMap::new(),
        },
    );
    let extracted = ExtractedCircuit { nets };

    let mut pre_measures = HashMap::new();
    pre_measures.insert("ugbw".to_string(), 100.0e6);
    let pre_sim = SimMeasurements {
        measures: pre_measures,
    };

    let mut post_measures = HashMap::new();
    post_measures.insert("ugbw".to_string(), 90.0e6);
    let post_sim = SimMeasurements {
        measures: post_measures,
    };

    let diff_pairs = vec![("VIP".to_string(), "VIN".to_string())];
    let report = evaluate_foms(&AnalysisInputs {
        geometry: Some(&geometry),
        extracted: Some(&extracted),
        diff_pairs: &diff_pairs,
        pre_sim: Some(&pre_sim),
        post_sim: Some(&post_sim),
    });

    assert!((report.diff_cap_imbalance_ratio.unwrap() - 0.01).abs() < 1e-9);
    assert!((report.bandwidth_retention_ratio.unwrap() - 0.90).abs() < 1e-9);
    assert!((report.area_efficiency.unwrap() - 0.42).abs() < 1e-9);
    assert!(report.is_satisfactory(0.02, 0.85));

    let ranked = rank_net_admittances(&extracted);
    assert_eq!(ranked.len(), 2);
    assert_eq!(ranked[0].net_name, "VIP");
    assert_eq!(ranked[1].net_name, "VIN");
}

#[test]
fn test_runners_workspace_and_sanitizers() {
    let base_dir = std::env::temp_dir().join("align_autotune_runner_test");
    let _ = fs::remove_dir_all(&base_dir);

    let ws = IterationWorkspace::prepare(&base_dir, 0).unwrap();
    assert!(ws.root_dir.ends_with("iter_0"));
    assert!(ws.design_dir.is_dir());

    // Test staging netlist + constraints into iter_0/design/
    let dummy_sp = base_dir.join("source_ota.sp");
    fs::write(
        &dummy_sp,
        ".subckt ota vip vin vout vdd vss\nM0 vout vip vss vss nmos w=1u l=90n nfin=4 nf=2\n.ends ota\n",
    )
    .unwrap();

    let constraints: Vec<Box<dyn SoftConstraint>> = vec![Box::new(SymmetricNets {
        direction: "V".to_string(),
        net1: "VIP".to_string(),
        net2: "VIN".to_string(),
        pins1: None,
        pins2: None,
    })];

    let staged_sp = stage_design_inputs("ota", &dummy_sp, &constraints, &ws.design_dir).unwrap();
    assert!(staged_sp.is_file());
    assert!(ws.design_dir.join("ota.const.json").is_file());

    // Test GDS discovery prefers real .gds over .python.gds
    fs::write(ws.root_dir.join("OTA_0.python.gds"), b"py").unwrap();
    fs::write(ws.root_dir.join("OTA_0.gds"), b"real").unwrap();
    let gds = find_top_gds(&args_or_path(&ws.root_dir), "ota").unwrap();
    assert_eq!(gds.file_name().unwrap().to_str().unwrap(), "OTA_0.gds");

    // Test top-level .subckt header normalization and schematic port extraction
    let ports = extract_subckt_ports(&fs::read_to_string(&staged_sp).unwrap(), "ota");
    assert_eq!(ports, vec!["vip", "vin", "vout", "vdd", "vss"]);

    let raw_magic = "* Magic extracted\n.subckt OTA_0 VOUT VDD VSS VIN VIP\nC0 VIP 0 1.2f\n.ends\n";
    let normalized = normalize_top_subckt_header(raw_magic, "ota", &ports);
    assert!(normalized.contains(".subckt ota vip vin vout vdd vss\n"));
    assert!(normalized.trim_end().ends_with(".ends ota"));

    let _ = fs::remove_dir_all(&base_dir);
}

fn args_or_path(p: &std::path::Path) -> PathBuf {
    p.to_path_buf()
}
