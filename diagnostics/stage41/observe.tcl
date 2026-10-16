# SPDX-License-Identifier: Apache-2.0
# READ-ONLY observation of the archived stage-41 OUTPUT database (sidecar section 9b).
# Same set-up as the pinned repair_design_postgrt.tcl up to the parasitics, then ONLY reports:
# no repair_design, no resize, no placement, no write_views / write_db. The archived ODB is mounted read-only.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
# stage41_dump (same record format as before) lives in the shared helper file
source $::env(STAGE41_DIAG_DIR)/records.tcl
# same report precision as the pinned sta/corner.tcl
set sta_report_default_digits 6

read_current_odb
set_propagated_clock [all_clocks]
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
stage41_dump $::env(STEP_DIR)/stage41-design-before.tsv

# Recorded BEFORE any reconstruction.
set have_routes [grt::have_routes]
puts "STAGE41_HAVE_ROUTES $have_routes"
if { $have_routes } {
    set mode HISTORIC_ROUTES_FROM_ODB
} else {
    # Same pinned helper and parameters as the original step; it writes after_grt.guide into STEP_DIR
    # (the fresh output directory), never into the archive. The result is a reconstruction, not a replay.
    source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
    set mode RECONSTRUCTED_GRT_IN_MEMORY
}
estimate_parasitics -global_routing
puts "STAGE41_PARASITICS_MODE $mode"

set names [lln::get_corner_names]
puts "STAGE41_CORNERS [llength $names] $names"
# Three separate reports per corner; every block is delimited, the counts come from the parsed rows.
foreach name $names {
    foreach {kind flag} {slew -max_slew cap -max_capacitance fanout -max_fanout} {
        puts "STAGE41_KIND_BEGIN $name $kind"
        report_check_types $flag -violators -corner $name
        puts "STAGE41_KIND_END $name $kind"
    }
}
stage41_dump $::env(STEP_DIR)/stage41-design-after.tsv
puts "STAGE41_DONE"
