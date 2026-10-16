# SPDX-License-Identifier: Apache-2.0
# READ-ONLY C2 calibration of the archived FINAL f6f4db6 database in a fresh process (sidecar section 9d):
# the stage-41 config, SDC and liberty files and ONLY the 3 final SPEF files, read with plain
# read_spef -corner (pinned OpenSTA Parasitics.tcl). No global route, no set_rc, no wire-RC estimate and no
# estimate_parasitics; no attempt to reset or delete parasitics. No resize, placement or netlist change and
# no write of any view.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(STAGE41_DIAG_DIR)/records.tcl
set sta_report_default_digits 6

read_current_odb
set_propagated_clock [all_clocks]
stage41_dump $::env(STEP_DIR)/c2-before.tsv

set drivers $::env(STAGE41_DRIVERS)
set names [lln::get_corner_names]
puts "PAIRED_CORNERS [llength $names] $names"
puts "PAIRED_C2_MODEL SPEF_ONLY_COLD_PROCESS"

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
stage41_meta C2 $drivers
stage41_probe C2 $drivers
stage41_dump $::env(STEP_DIR)/c2-after.tsv
puts "C2_DONE"
