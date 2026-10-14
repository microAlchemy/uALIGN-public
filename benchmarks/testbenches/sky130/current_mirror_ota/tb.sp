* sky130 current_mirror_ota AC testbench (official SkyWater BSIM4v5 models)
.title sky130_current_mirror_ota_tb

.option scale=1e-6
.param mc_mm_switch=0
.param mc_pr_switch=0
.param sky130_fd_pr__pfet_01v8__wlod_diff=0.0
.param sky130_fd_pr__pfet_01v8__kvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8__lkvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8__wkvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8__ku0_diff=0.0
.param sky130_fd_pr__pfet_01v8__lku0_diff=0.0
.param sky130_fd_pr__pfet_01v8__wku0_diff=0.0
.param sky130_fd_pr__pfet_01v8__kvsat_diff=0.0

.include "sky130_fd_pr__nfet_01v8__mismatch.corner.spice"
.include "sky130_fd_pr__nfet_01v8__tt.corner.spice"
.include "sky130_fd_pr__pfet_01v8__mismatch.corner.spice"
.include "sky130_fd_pr__pfet_01v8__tt.corner.spice"

.include "extracted.spice"

* supplies
Vdd vdd 0 1.8
Vss vss 0 0

* bias
Vbias id vss 0.85

* inputs
Vinn vinn vss 0.9
Vinp vinp vss 0.9 AC 1

* DUT
Xota vss vdd vout vinn vinp id current_mirror_ota

.ac dec 20 1k 10g
.print ac vdb(vout)

.measure ac gain_lin MAX vmag(vout)
.measure ac ugbw WHEN vmag(vout)=1 CROSS=1

.end
