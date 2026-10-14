# align-autotune

`align-autotune` is a closed-loop layout tuning wrapper for [ALIGN](https://github.com/ALIGN-analoglayout/ALIGN-public). It orchestrates ALIGN layout generation, Magic parasitic extraction (PEX), and ngspice simulation to evaluate post-layout Figures of Merit (such as differential capacitance imbalance and bandwidth retention) and iteratively refine `.const.json` layout constraints until target specifications are met.

## Usage

Run from the `align-autotune/` directory:

```bash
# Build
cargo build

# Run tests
cargo test

# View CLI flags
cargo run -- --help

# Run the autotune loop
cargo run -- \
  --circuit five_transistor_ota \
  --netlist ../examples/five_transistor_ota/five_transistor_ota.sp \
  --pdk ../pdks/FinFET14nm_Mock_PDK \
  --work-dir ./autotune_work \
  --max-iters 3
```
