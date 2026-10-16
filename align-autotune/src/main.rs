use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;

use align_autotune::config::{default_reference_nets, AutoTuneConfig};
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

    #[arg(
        short,
        long,
        default_value = "../pdks/SKY130_PDK",
        help = "Path to PDK directory (defaults to SKY130_PDK)"
    )]
    pdk: PathBuf,

    #[arg(
        short,
        long,
        help = "Optional path to SPICE testbench (tb.sp); auto-discovered if omitted"
    )]
    testbench: Option<PathBuf>,

    #[arg(
        short,
        long,
        default_value = "./autotune_work",
        help = "Working directory"
    )]
    work_dir: PathBuf,

    #[arg(short, long, default_value_t = 3, help = "Maximum tuning iterations")]
    max_iters: usize,

    #[arg(
        long,
        value_delimiter = ',',
        default_values_t = default_reference_nets(),
        help = "Comma-separated AC ground/power reference nets (overrides defaults)"
    )]
    ref_nets: Vec<String>,
}

fn main() -> Result<()> {
    let args = Args::parse();

    let config = AutoTuneConfig {
        circuit: args.circuit,
        netlist_path: args.netlist,
        pdk_path: args.pdk,
        testbench_path: args.testbench,
        work_dir: args.work_dir,
        max_iterations: args.max_iters,
        reference_nets: args.ref_nets,
    };

    println!("Starting ALIGN autotune for circuit: {}", config.circuit);
    let summary = run_autotune_loop(&config)?;
    println!("Autotune completed: {summary}");

    Ok(())
}
