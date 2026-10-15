* sky130 telescopic_ota AC testbench (official SkyWater BSIM4v5 models)
.title sky130_telescopic_ota_tb

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
.param sky130_fd_pr__nfet_01v8_lvt__wlod_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__kvth0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__lkvth0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__wkvth0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__ku0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__lku0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__wku0_diff=0.0
.param sky130_fd_pr__nfet_01v8_lvt__kvsat_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__wlod_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__kvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__lkvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__wkvth0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__ku0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__lku0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__wku0_diff=0.0
.param sky130_fd_pr__pfet_01v8_lvt__kvsat_diff=0.0

.include "sky130_fd_pr__nfet_01v8__mismatch.corner.spice"
.include "sky130_fd_pr__nfet_01v8__tt.corner.spice"
.include "sky130_fd_pr__pfet_01v8__mismatch.corner.spice"
.include "sky130_fd_pr__pfet_01v8__tt.corner.spice"
.include "sky130_fd_pr__nfet_01v8_lvt__mismatch.corner.spice"
.include "sky130_fd_pr__nfet_01v8_lvt__tt.corner.spice"
.include "sky130_fd_pr__pfet_01v8_lvt__mismatch.corner.spice"
.include "sky130_fd_pr__pfet_01v8_lvt__tt.corner.spice"

.include "extracted.spice"

* supplies
Vdd vdd 0 1.8

* bias voltages
Ibias vdd id dc 10u
Vbiasn vbiasn 0 1.0
Vbiasp1 vbiasp1 0 0.8
Vbiasp2 vbiasp2 0 1.0

* inputs
Vinn vinn 0 0.9
Vinp vinp 0 0.9 AC 1

* DUT
Xota id vbiasn vbiasp1 vbiasp2 vinn vinp voutn voutp vdd 0 telescopic_ota

.ac dec 20 1k 10g
.print ac vdb(voutn)

.measure ac gain_lin MAX vmag(voutn)
.measure ac ugbw WHEN vmag(voutn)=1 CROSS=1

.end
