# SPDX-License-Identifier: Apache-2.0
# Shared read-only helpers of the stage-41 diagnostics (sidecar sections 9b and 9c).

# Deterministic records of the logical design and placement, read from OpenDB:
#   N <inst> <master> <x> <y> <orient> <placement status>
#   I <net> <inst> <mterm> <io type> <master>
#   B <net> <bterm> <io type>
# Fields are tab-separated; names are written as OpenDB returns them.
proc stage41_dump {path} {
    # same access path as the pinned io.tcl set_global_vars
    set block [[[::ord::get_db] getChip] getBlock]
    set fh [open $path w]
    foreach inst [$block getInsts] {
        set loc [$inst getLocation]
        puts $fh [join [list N [$inst getName] [[$inst getMaster] getName] [lindex $loc 0] [lindex $loc 1] \
            [$inst getOrient] [$inst getPlacementStatus]] "\t"]
    }
    foreach net [$block getNets] {
        set nname [$net getName]
        foreach it [$net getITerms] {
            set inst [$it getInst]
            set mterm [$it getMTerm]
            puts $fh [join [list I $nname [$inst getName] [$mterm getName] [$mterm getIoType] \
                [[$inst getMaster] getName]] "\t"]
        }
        foreach bt [$net getBTerms] {
            puts $fh [join [list B $nname [$bt getName] [$bt getIoType]] "\t"]
        }
    }
    close $fh
}

# The single STA pin of a path, or "" when get_pins fails or does not return exactly one pin.
proc stage41_one_pin {path} {
    if { [catch {get_pins $path} pins] || [llength $pins] != 1 } {
        return ""
    }
    return [lindex $pins 0]
}

# Clock names reaching a pin, from the STA clock set (get_property <pin> clocks; Property.cc). Returns
# "NONE" for an empty successful query and "UNKNOWN" if the pin is not unique or any query fails.
proc stage41_clock_names {path} {
    set pin [stage41_one_pin $path]
    if { $pin eq "" || [catch {get_property $pin clocks} clks] } {
        return UNKNOWN
    }
    set names [list]
    foreach clk $clks {
        if { [catch {get_property $clk name} n] } {
            return UNKNOWN
        }
        lappend names $n
    }
    if { [llength $names] == 0 } {
        return NONE
    }
    return [join $names ","]
}

# One OpenDB accessor call; UNKNOWN when the object is unknown or the accessor is not available in Tcl,
# NONE for an empty result. Spaces are replaced so that the value stays one token.
proc stage41_odb {obj method} {
    if { $obj eq "UNKNOWN" || [catch {$obj $method} v] } {
        return UNKNOWN
    }
    if { $v eq "" } {
        return NONE
    }
    return [string map {" " _} $v]
}

# Corner-independent metadata per driver pin: master, master type (dbMaster getType), instance and net
# dont-touch flags, net name and the STA clock set. Accessors that are not available stay UNKNOWN.
proc stage41_meta {mode drivers} {
    set block [[[::ord::get_db] getChip] getBlock]
    set insts [dict create]
    foreach inst [$block getInsts] {
        dict set insts [$inst getName] $inst
    }
    foreach drv $drivers {
        set slash [string last "/" $drv]
        set iname [string range $drv 0 [expr {$slash - 1}]]
        set term [string range $drv [expr {$slash + 1}] end]
        if { ![dict exists $insts $iname] } {
            puts "PAIRED_META $mode $drv ABSENT"
            continue
        }
        set inst [dict get $insts $iname]
        set master [$inst getMaster]
        set net UNKNOWN
        foreach it [$inst getITerms] {
            if { [[$it getMTerm] getName] eq $term } {
                if { [catch {$it getNet} n] || $n eq "" || $n eq "NULL" } {
                    set net UNKNOWN
                } else {
                    set net $n
                }
            }
        }
        puts "PAIRED_META $mode $drv master=[$master getName] type=[stage41_odb $master getType]\
inst_dont_touch=[stage41_odb $inst isDoNotTouch] net=[stage41_odb $net getName]\
net_dont_touch=[stage41_odb $net isDoNotTouch] clocks=[stage41_clock_names $drv]"
    }
}

# Exact per-corner slew of the first graph vertex of a pin (Network.i Pin.vertices, Graph.i
# Vertex.slew_corner -> Sta::vertexSlew(vertex, rf, corner, min_max)), as the raw value returned by the
# SWIG wrapper; its unit is calibrated in the summary against report_check_types. UNKNOWN on any failure.
proc stage41_corner_slew {path corner_obj rf} {
    set pin [stage41_one_pin $path]
    if { $pin eq "" || $corner_obj eq "NULL" || [catch {$pin vertices} verts] } {
        return UNKNOWN
    }
    set vertex [lindex $verts 0]
    if { $vertex eq "" || $vertex eq "NULL" || [catch {$vertex slew_corner $rf $corner_obj "max"} v] } {
        return UNKNOWN
    }
    return $v
}

# Per-driver probe in the current parasitic state. The pin slew properties (get_property slew_max_*) take the
# worst over ALL analysis points (Property.cc / Sta::vertexSlew without corner), so they are printed once per
# driver with that explicit scope and never under a corner. Per corner: exact vertex slews and report_net.
proc stage41_probe {mode drivers} {
    foreach drv $drivers {
        set inst [string range $drv 0 [expr {[string last "/" $drv] - 1}]]
        foreach {label path} [list out $drv in "$inst/A"] {
            set pin [stage41_one_pin $path]
            foreach prop {slew_max_rise slew_max_fall} {
                if { $pin eq "" || [catch {get_property $pin $prop} v] } {
                    set v UNKNOWN
                }
                puts "PAIRED_GLOBAL_WORST_ALL_CORNERS $mode $drv $label $prop $v"
            }
        }
    }
    foreach corner [lln::get_corner_names] {
        if { [catch {sta::find_corner $corner} corner_obj] } {
            set corner_obj NULL
        }
        foreach drv $drivers {
            puts "PAIRED_PROBE_BEGIN $mode $corner $drv"
            set inst [string range $drv 0 [expr {[string last "/" $drv] - 1}]]
            foreach {label path} [list out $drv in "$inst/A"] {
                foreach rf {rise fall} {
                    puts "PAIRED_SLEW $label $rf [stage41_corner_slew $path $corner_obj $rf]"
                }
            }
            if { [stage41_one_pin $drv] eq "" } {
                puts "PAIRED_NET_UNKNOWN"
            } elseif { [catch {report_net -corner $corner -digits 6 $drv} err] } {
                puts "PAIRED_NET_UNKNOWN"
            }
            puts "PAIRED_PROBE_END $mode $corner $drv"
        }
    }
}

# Three explicit violator reports per corner, delimited by markers carrying the mode.
proc stage41_reports {mode} {
    foreach name [lln::get_corner_names] {
        foreach {kind flag} {slew -max_slew cap -max_capacitance fanout -max_fanout} {
            puts "PAIRED_KIND_BEGIN $mode $name $kind"
            report_check_types $flag -violators -corner $name
            puts "PAIRED_KIND_END $mode $name $kind"
        }
    }
}
