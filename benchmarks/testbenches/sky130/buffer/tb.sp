* sky130 buffer transient testbench (official SkyWater BSIM4v5 models)
.title sky130_buffer_tb

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

* input pulse
Vin in vss PULSE(0 1.8 0.5n 0.05n 0.05n 1n 2n)

* DUT
Xbuf vss vdd in out buffer

* load
Cload out 0 10f

.tran 10p 4n

.measure tran tphl_ns TRIG v(in) VAL=0.9 FALL=1 TARG v(out) VAL=0.9 FALL=1
.measure tran tplh_ns TRIG v(in) VAL=0.9 RISE=1 TARG v(out) VAL=0.9 RISE=1

.end
