# ext2spice.tcl — reads INPUT_GDS, flattens into CIRCUIT, extracts devices + RC parasitics
# Usage: magic -dnull -noconsole -T SKY130.tech < ext2spice.tcl
# Environment variables set before invocation:
#   INPUT_GDS  — absolute path to GDS file
#   OUTPUT_DIR — directory to write extracted.spice
#   CIRCUIT    — top-level circuit name

set input_gds $env(INPUT_GDS)
set output_dir $env(OUTPUT_DIR)
set circuit $env(CIRCUIT)

gds readonly true
gds read $input_gds

# Discover the top-level cell while filtering out Magic's default "(UNNAMED)" buffer
set top_candidates [lsearch -all -inline -not [cellname list top] "(UNNAMED)"]
set all_candidates [lsearch -all -inline -not [cellname list allcells] "(UNNAMED)"]
set circuit_upper [string toupper $circuit]
set topcell ""

foreach cell $all_candidates {
    if {[string match "${circuit_upper}_*" $cell] || [string equal -nocase $cell $circuit]} {
        set topcell $cell
        break
    }
}
if {$topcell eq "" && [llength $top_candidates] > 0} {
    set topcell [lindex $top_candidates 0]
}
if {$topcell eq "" && [llength $all_candidates] > 0} {
    set topcell [lindex $all_candidates 0]
}

puts "ext2spice: loading and flattening top cell '$topcell' -> '$circuit'"
load $topcell
select top cell
expand
flatten ${topcell}_flat
load ${topcell}_flat
cellname rename ${topcell}_flat $circuit
select top cell

# Extract devices and full lumped R/C parasitics
extract all
ext2spice lvs
ext2spice rthresh 0
ext2spice cthresh 0
ext2spice subcircuit top on
ext2spice format ngspice
ext2spice -o ${output_dir}/extracted.spice

# Clean up intermediate .ext files
foreach f [glob -nocomplain *.ext] { file delete $f }

quit
