# SPDX-License-Identifier: Apache-2.0
# READ-ONLY metadata of the archived stage-39 database (input of stage 41), sidecar section 9c / C3.
# Records (masters, loads) for the whole design, and for the 8 stage-41 drivers the master type, dont-touch
# flags and the STA clock set. No parasitics, no reports of limits, no write of any view.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(STAGE41_DIAG_DIR)/records.tcl

read_current_odb
stage41_dump $::env(STEP_DIR)/c3-design.tsv
stage41_meta C3 $::env(STAGE41_DRIVERS)
puts "C3_DONE"
