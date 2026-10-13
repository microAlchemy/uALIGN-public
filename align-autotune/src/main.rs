use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;

use align_autotune::config::{AutoTuneConfig, TargetFoMs};
use align_autotune::flow::run_autotune_loop;

#[derive(Parser, Debug)]
#[command(
    name = "align-autotune",
    about = "Automated closed-loop layout tuning wrapper for ALIGN using Magic PEX and FoM analysis"
)]
struct Args {
    #[arg(short, long, help = "Circuit / subcircuit name")]
    circuit: String,

    #[arg(short, long, help = "Path to SPICE netlist")]
    netlist: PathBuf,

    #[arg(short, long, help = "Path to PDK directory")]
    pdk: PathBuf,

    #[arg(
        short,
        long,
        default_value = "./autotune_work",
        help = "Working directory"
    )]
    work_dir: PathBuf,

    #[arg(short, long, default_value_t = 3, help = "Maximum tuning iterations")]
    max_iters: usize,
}

fn main() -> Result<()> {
    let args = Args::parse();

    let config = AutoTuneConfig {
        circuit: args.circuit,
        netlist_path: args.netlist,
        pdk_path: args.pdk,
        work_dir: args.work_dir,
        max_iterations: args.max_iters,
        targets: TargetFoMs::default(),
    };

    println!("Starting ALIGN autotune for circuit: {}", config.circuit);
    let summary = run_autotune_loop(&config)?;
    println!("Autotune completed: {:?}", summary);

    Ok(())
}
