# align-autotune

`align-autotune` is a closed-loop layout tuning wrapper for [ALIGN](https://github.com/ALIGN-analoglayout/ALIGN-public). It orchestrates ALIGN layout generation, Magic parasitic extraction (PEX), and ngspice simulation to evaluate post-layout Figures of Merit (such as differential capacitance imbalance and bandwidth retention) and iteratively refine `.const.json` layout constraints until target specifications are met. However, ngspice simulation must be provided for the simulation required FoM's. 

## Prerequisites & Setup

1. **Rust Toolchain:** Rust 1.75+ (`cargo`).
2. **External EDA Tools (on `PATH`):**
   - `schematic2layout.py` (from the ALIGN Python environment)
   - `magic` (8.3+)
   - `ngspice` (40+)
3. **SkyWater 130nm PDK (`sky130A`):**
   - **ALIGN PDK rules & generators:** `pdks/SKY130_PDK`
   - **Magic extraction tech file:** bundled at `benchmarks/magic_tech/SKY130_PDK/SKY130.tech` (`sky130A`)
   - **Official BSIM4v5 SPICE models:** install `sky130A` via [ciel](https://github.com/fossi-foundation/ciel) or [volare](https://github.com/efabless/volare), or set `PDK_ROOT` / `SKY130_PDK_ROOT`:
     ```bash
     pip install ciel
     ciel enable --pdk-family sky130 0fe599b2afb6708d281543108caf8310912f54af
     export PDK_ROOT="$HOME/.ciel"
     ```
     `NgspiceRunner` automatically discovers `$SKY130_PDK_ROOT`, `$PDK_ROOT/sky130A`, `~/.ciel/sky130A`, or `~/.volare/sky130A` and writes `.spiceinit` (`set ngbehavior=hsa`, `sourcepath`) in each simulation workspace.

## Usage

Run from the `align-autotune/` directory:

```bash
# Build
cargo build

# Run tests
cargo test

# View CLI flags
cargo run -- --help

# Run the autotune loop (--pdk defaults to ../pdks/SKY130_PDK)
# Default reference nets: 0,sub,gnd,vss,vgnd,vsubs,vdd,vpwr (override with --ref-nets <csv>)
cargo run -- \
  --circuit five_transistor_ota \
  --netlist ../examples/five_transistor_ota/five_transistor_ota.sp \
  --testbench ../benchmarks/testbenches/sky130/five_transistor_ota/tb.sp \
  --work-dir ./autotune_work \
  --max-iters 3
```
