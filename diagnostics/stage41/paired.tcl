# SPDX-License-Identifier: Apache-2.0
# READ-ONLY paired observation of the archived FINAL f6f4db6 database (sidecar section 9c).
# One process, one database, the stage-41 config, SDC and liberty files; no resize, placement or netlist
# change and no write of any view. Order as the pinned sta/corner.tcl: read_current_odb, set_rc, the
# global-route estimate (C1), then the final SPEF files per corner (C2).
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(STAGE41_DIAG_DIR)/records.tcl
set sta_report_default_digits 6

read_current_odb
set_propagated_clock [all_clocks]
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl
stage41_dump $::env(STEP_DIR)/paired-c1-before.tsv

set drivers $::env(STAGE41_DRIVERS)
set names [lln::get_corner_names]
puts "PAIRED_CORNERS [llength $names] $names"

# ---- C1: global-route estimate on the final database ----
set have_routes [grt::have_routes]
puts "PAIRED_HAVE_ROUTES $have_routes"
if { $have_routes } {
    set mode HISTORIC_ROUTES_FROM_ODB
} else {
    # pinned helper and parameters; writes after_grt.guide into STEP_DIR only. A reconstruction, not a replay.
    source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
    set mode RECONSTRUCTED_GRT_IN_MEMORY
}
estimate_parasitics -global_routing
puts "PAIRED_C1_MODE $mode"
stage41_reports C1
stage41_meta C1 $drivers
stage41_probe C1 $drivers
stage41_dump $::env(STEP_DIR)/paired-c1-after.tsv

# ---- C2: the archived final SPEF files on the SAME database, explicitly per corner ----
foreach name $names {
    set rc [string toupper [lindex [split $name _] 0]]
    set spef $::env(STAGE41_SPEF_$rc)
    puts "PAIRED_SPEF_BEGIN $name $rc $spef"
    read_spef -corner $name $spef
    puts "PAIRED_SPEF_END $name"
}
stage41_reports C2
foreach name $names {
    lln::set_sta_cmd_corner $name
    puts "PAIRED_ANNOT_BEGIN $name"
    report_parasitic_annotation -report_unannotated
    puts "PAIRED_ANNOT_END $name"
}
stage41_probe C2 $drivers
stage41_dump $::env(STEP_DIR)/paired-c2-after.tsv
puts "PAIRED_DONE"
