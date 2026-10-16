# SPDX-License-Identifier: Apache-2.0
# READ-ONLY C1 observation of the archived FINAL f6f4db6 database (sidecar sections 9c, 9d): the stage-41
# config, SDC and liberty files, the global-route estimate only. No SPEF is read in this process (C2 runs in a
# separate fresh process, c2.tcl). No resize, placement or netlist change and no write of any view.
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
puts "PAIRED_DONE"
