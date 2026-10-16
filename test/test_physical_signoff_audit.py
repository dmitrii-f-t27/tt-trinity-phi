#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of test/physical_signoff_audit.py on SYNTHETIC fixtures written into a temporary directory.

The "good" fixture only shows that the parser accepts a well-formed run; it is NOT physical evidence of
anything (its numbers are made up). Every other case must FAIL. Standard library only.
Usage (repo root): python3 test/test_physical_signoff_audit.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import physical_signoff_audit as psa  # noqa: E402

SIDECAR_TEXT = """pub const SIGNOFF_STA_CORNERS : u32 = 9;
pub const SIGNOFF_MAX_SLEW_PS : u32 = 750;
pub const SIGNOFF_MAX_FANOUT : u32 = 10;
pub const SIGNOFF_ALLOWED_VIOLATIONS : u32 = 0;
"""


# cells that the auditor's EXTRA exclusion list (sections 9e / 9g) must NOT match: stronger buffers, NOR2
# sizes and a NOR2B cell (buf_12 / clkbuf_16 share a prefix with excluded exact names). This only
# shows they are not caught by these entries; it says nothing about the official no_synth / drc_exclude
# filters, which stay unchanged and are applied by the flow.
ALLOWED_NETLIST = ("sky130_fd_sc_hd__buf_2 u1 (.A(a), .X(x));\nsky130_fd_sc_hd__clkbuf_4 u2 (.A(a), .X(y));\n"
                   "sky130_fd_sc_hd__buf_12 u3 (.A(a), .X(z));\nsky130_fd_sc_hd__clkbuf_16 u4 (.A(a), .X(w));\n"
                   "sky130_fd_sc_hd__nor2_4 u5 (.A(a), .B(b), .Y(p));\n"
                   "sky130_fd_sc_hd__nor2_8 u6 (.A(a), .B(b), .Y(q));\n"
                   "sky130_fd_sc_hd__nor2b_1 u7 (.A(a), .B_N(b), .Y(r));\n")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def footer(slew=0, fanout=0, cap=0):
    return ("max slew violation count %d\nmax fanout violation count %d\nmax cap violation count %d\n"
            % (slew, fanout, cap))


class Fixture:
    """A well-formed synthetic run; tests mutate it before auditing."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="psa-")
        self.root = os.path.join(self.tmp, "repo")
        self.run = os.path.join(self.tmp, "runs", "wokwi")
        write(os.path.join(self.root, psa.SIDECAR), SIDECAR_TEXT)
        write(os.path.join(self.run, "06-yosys-synthesis", "config.json"),
              {"meta": {"step": "Yosys.Synthesis"}, "EXTRA_EXCLUDED_CELLS": psa.EXCLUDED})
        cfg = dict(psa.EXPECT_CFG, STA_CORNERS=psa.CORNERS, EXTRA_EXCLUDED_CELLS=psa.EXCLUDED,
                   meta={"step": "OpenROAD.STAPostPNR"})
        self.sta = os.path.join(self.run, "57-openroad-stapostpnr")
        write(os.path.join(self.sta, "config.json"), cfg)
        self.metrics = {}
        for c in psa.CORNERS:
            for name in psa.CORNER_COUNTS:
                self.metrics["%s__corner:%s" % (name, c)] = 0
            self.metrics["timing__setup__ws__corner:%s" % c] = 1.5
            self.metrics["timing__hold__ws__corner:%s" % c] = 0.2
            write(os.path.join(self.sta, c, "checks.rpt"), footer())
        self.save_state()
        for i, (step, var) in enumerate(psa.CHECKERS.items()):
            write(os.path.join(self.run, "%d-checker" % (80 + i), "config.json"),
                  {"meta": {"step": step}, var: ["*"], "TIMING_VIOLATION_CORNERS": ["*"]})
        self.final = {"magic__drc_error__count": 0, "design__lvs_error__count": 0,
                      "route__antenna_violation__count": 0, "design__instance__utilization": 0.6,
                      "design__critical_disconnected_pin__count": 0}
        self.save_final()
        self.cts = os.path.join(self.run, "35-openroad-cts")
        self.save_cts(dict(psa.EXPECT_CTS))
        self.grt = os.path.join(self.run, "41-openroad-repairdesignpostgrt")
        self.save_grt(dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_RUN_GRT=True))
        write(os.path.join(self.run, "final", "nl", "top.nl.v"), ALLOWED_NETLIST)

    def save_cts(self, cfg):
        write(os.path.join(self.cts, "config.json"), dict(cfg, meta={"step": "OpenROAD.CTS"}))

    def save_grt(self, cfg):
        write(os.path.join(self.grt, "config.json"), dict(cfg, meta={"step": "OpenROAD.RepairDesignPostGRT"}))

    def save_state(self):
        write(os.path.join(self.sta, "state_out.json"), {"metrics": self.metrics})

    def save_final(self):
        write(os.path.join(self.run, "final", "metrics.json"), self.final)

    def audit(self):
        return psa.Audit(self.run, self.root).run()

    def close(self):
        shutil.rmtree(self.tmp)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def assertFails(self, needle):
        rep = self.fx.audit()
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertTrue(any(needle in p for p in rep["problems"]), rep["problems"])
        return rep

    def test_wellformed_fixture_parses(self):
        # parser check on made-up numbers only, not physical evidence
        rep = self.fx.audit()
        self.assertEqual(rep["verdict"], "PASS", rep["problems"])
        # buf_2, clkbuf_4, buf_12, clkbuf_16, nor2_4, nor2_8 and nor2b_1 match none of the 6 exclusion entries
        self.assertEqual(rep["grt_repair"]["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"], 50)
        self.assertEqual(rep["excluded_cell_hits"], {})

    def test_absent_corner_fails(self):
        c = psa.CORNERS[7]
        shutil.rmtree(os.path.join(self.fx.sta, c))
        for k in [k for k in self.fx.metrics if k.endswith(":" + c)]:
            del self.fx.metrics[k]
        self.fx.save_state()
        rep = self.assertFails(c)
        # no aggregate may be invented from eight corners
        self.assertIsNone(rep["max_over_corners"]["design__max_slew_violation__count"])
        self.assertIsNone(rep["totals"]["design__max_fanout_violation__count"])

    def test_missing_metric_gives_no_aggregate(self):
        del self.fx.metrics["design__max_cap_violation__count__corner:%s" % psa.CORNERS[2]]
        self.fx.save_state()
        rep = self.assertFails("design__max_cap_violation__count__corner:%s" % psa.CORNERS[2])
        self.assertIsNone(rep["max_over_corners"]["design__max_cap_violation__count"])
        self.assertIsNone(rep["totals"]["design__max_cap_violation__count"])
        self.assertEqual(rep["known_max_partial"]["design__max_cap_violation__count"], 0)

    def test_malformed_meta_fails_closed(self):
        write(os.path.join(self.fx.run, "90-odd", "config.json"), {"meta": ["not", "an", "object"]})
        write(os.path.join(self.fx.run, "91-odd", "config.json"), [1, 2])
        rep = self.assertFails("has no meta object")
        self.assertEqual(sum("has no meta object" in p for p in rep["problems"]), 2)

    def test_nonfinite_metric_fails(self):
        path = os.path.join(self.fx.sta, "state_out.json")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        key = '"timing__setup__ws__corner:%s": 1.5' % psa.CORNERS[0]
        self.assertIn(key, text)
        write(path, text.replace(key, key.replace("1.5", "NaN")))
        self.assertFails("unreadable")

    def test_report_metric_mismatch_fails(self):
        write(os.path.join(self.fx.sta, psa.CORNERS[4], "checks.rpt"), footer(slew=3))
        self.assertFails("!= metric")

    def test_clock_fanout_nonzero_fails(self):
        c = psa.CORNERS[0]
        self.fx.metrics["design__max_fanout_violation__count__corner:%s" % c] = 1
        self.fx.save_state()
        write(os.path.join(self.fx.sta, c, "checks.rpt"), footer(fanout=1))
        self.assertFails("design__max_fanout_violation__count = 1")

    def test_slew_violation_fails(self):
        c = "max_ss_100C_1v60"
        self.fx.metrics["design__max_slew_violation__count__corner:%s" % c] = 167
        self.fx.save_state()
        write(os.path.join(self.fx.sta, c, "checks.rpt"), footer(slew=167))
        rep = self.assertFails("design__max_slew_violation__count = 167")
        self.assertEqual(rep["max_over_corners"]["design__max_slew_violation__count"], 167)

    def test_missing_final_metrics_fails(self):
        os.remove(os.path.join(self.fx.run, "final", "metrics.json"))
        self.assertFails("final/metrics.json missing")

    def test_excluded_cell_in_netlist_fails(self):
        write(os.path.join(self.fx.run, "final", "nl", "top.nl.v"),
              "sky130_fd_sc_hd__clkdlybuf4s25_1 fanout80 (.A(a), .X(x));\n")
        self.assertFails("excluded cells still in the final netlist")

    def test_dlygate_in_netlist_fails(self):
        # the cell that drove all 8 max_ss slew violations at 9eb7373
        write(os.path.join(self.fx.run, "final", "pnl", "top.pnl.v"),
              "sky130_fd_sc_hd__buf_2 u1 (.A(a), .X(x));\nsky130_fd_sc_hd__dlygate4sd3_1 fanout79 (.A(a), .X(y));\n")
        rep = self.assertFails("excluded cells still in the final netlist")
        self.assertIn("sky130_fd_sc_hd__dlygate4sd3_1 (sky130_fd_sc_hd__dlygate*) in top.pnl.v",
                      rep["excluded_cell_hits"])

    def test_dlymetal_in_netlist_fails(self):
        write(os.path.join(self.fx.run, "final", "nl", "top.nl.v"),
              "sky130_fd_sc_hd__dlymetal6s4s_1 u7 (.A(a), .X(x));\n")
        rep = self.assertFails("excluded cells still in the final netlist")
        self.assertEqual(rep["excluded_cell_hits"],
                         {"sky130_fd_sc_hd__dlymetal6s4s_1 (sky130_fd_sc_hd__dlymetal*) in top.nl.v": 1})

    def test_relaxed_checker_corners_fail(self):
        d = os.path.join(self.fx.run, "82-checker")
        write(os.path.join(d, "config.json"), {"meta": {"step": "Checker.MaxSlewViolations"},
                                               "MAX_SLEW_VIOLATION_CORNERS": [""],
                                               "TIMING_VIOLATION_CORNERS": ["*"]})
        self.assertFails("Checker.MaxSlewViolations checks corners")

    def test_negative_slack_fails(self):
        self.fx.metrics["timing__hold__ws__corner:%s" % psa.CORNERS[5]] = -0.01
        self.fx.save_state()
        self.assertFails("is negative")

    def test_missing_exclusion_fails(self):
        write(os.path.join(self.fx.run, "06-yosys-synthesis", "config.json"),
              {"meta": {"step": "Yosys.Synthesis"}, "EXTRA_EXCLUDED_CELLS": None})
        self.assertFails("synthesis EXTRA_EXCLUDED_CELLS")

    def test_each_drive_floor_cell_in_netlist_fails(self):
        for cell in ("sky130_fd_sc_hd__buf_1", "sky130_fd_sc_hd__clkbuf_1", "sky130_fd_sc_hd__clkbuf_2"):
            with self.subTest(cell=cell):
                write(os.path.join(self.fx.run, "final", "nl", "top.nl.v"),
                      ALLOWED_NETLIST + "%s fanout66 (.A(a), .X(v));\n" % cell)
                rep = self.assertFails("excluded cells still in the final netlist")
                self.assertEqual(rep["excluded_cell_hits"], {"%s (%s) in top.nl.v" % (cell, cell): 1})

    def test_old_three_exclusions_fail(self):
        # the f6f4db6 list without the drive floor is not accepted any more
        cfg = dict(psa.EXPECT_CFG, STA_CORNERS=psa.CORNERS, EXTRA_EXCLUDED_CELLS=psa.EXCLUDED[:3],
                   meta={"step": "OpenROAD.STAPostPNR"})
        write(os.path.join(self.fx.sta, "config.json"), cfg)
        self.assertFails("PnR EXTRA_EXCLUDED_CELLS")

    # The 9f NOR2 floor (nor2_1, nor2_2) failed in CI and was removed by section 9g; its evidence is kept in
    # Git and the private records, not as a test that would still demand it.
    def test_6_entry_list_does_not_match_nor2_names(self):
        write(os.path.join(self.fx.run, "final", "nl", "top.nl.v"),
              ALLOWED_NETLIST + "sky130_fd_sc_hd__nor2_1 u8 (.A(a), .B(b), .Y(s));\n"
              "sky130_fd_sc_hd__nor2_2 _1809_ (.A(a), .B(b), .Y(_0364_));\n")
        rep = self.fx.audit()
        self.assertEqual(rep["excluded_cell_hits"], {})
        self.assertEqual(rep["verdict"], "PASS", rep["problems"])

    def test_removed_9f_8_entry_list_fails(self):
        nine_f = psa.EXCLUDED + ["sky130_fd_sc_hd__nor2_1", "sky130_fd_sc_hd__nor2_2"]
        cfg = dict(psa.EXPECT_CFG, STA_CORNERS=psa.CORNERS, EXTRA_EXCLUDED_CELLS=nine_f,
                   meta={"step": "OpenROAD.STAPostPNR"})
        write(os.path.join(self.fx.sta, "config.json"), cfg)
        write(os.path.join(self.fx.run, "06-yosys-synthesis", "config.json"),
              {"meta": {"step": "Yosys.Synthesis"}, "EXTRA_EXCLUDED_CELLS": nine_f})
        rep = self.assertFails("PnR EXTRA_EXCLUDED_CELLS")
        self.assertTrue(any("synthesis EXTRA_EXCLUDED_CELLS" in p for p in rep["problems"]))

    def test_missing_grt_repair_step_fails(self):
        shutil.rmtree(self.fx.grt)
        self.assertFails("expected exactly one OpenROAD.RepairDesignPostGRT step directory, found 0")

    def test_grt_wire_length_missing_zero_wrong_or_bool_fails(self):
        for bad in (None, 0, 0.0, 100, 49.9, True, "50"):
            with self.subTest(value=bad):
                cfg = dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_RUN_GRT=True)
                if bad is None:
                    del cfg["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"]
                else:
                    cfg["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] = bad
                self.fx.save_grt(cfg)
                self.assertFails("post-GRT repair GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH")

    def test_grt_wire_length_50_as_decimal_accepted(self):
        self.fx.save_grt(dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH=50.0,
                              GRT_DESIGN_REPAIR_RUN_GRT=True))
        rep = self.fx.audit()
        self.assertEqual(rep["verdict"], "PASS", rep["problems"])

    def test_grt_margin_wrong_fails(self):
        self.fx.save_grt(dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_MAX_SLEW_PCT=20,
                              GRT_DESIGN_REPAIR_RUN_GRT=True))
        self.assertFails("post-GRT repair GRT_DESIGN_REPAIR_MAX_SLEW_PCT")
        self.fx.save_grt(dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_MAX_CAP_PCT=None,
                              GRT_DESIGN_REPAIR_RUN_GRT=True))
        self.assertFails("post-GRT repair GRT_DESIGN_REPAIR_MAX_CAP_PCT")

    def test_grt_run_grt_false_or_not_bool_fails(self):
        for bad in (False, 1, "true", None):
            with self.subTest(value=bad):
                self.fx.save_grt(dict(psa.EXPECT_GRT_REPAIR, GRT_DESIGN_REPAIR_RUN_GRT=bad))
                self.assertFails("post-GRT repair GRT_DESIGN_REPAIR_RUN_GRT")

    def test_missing_cts_step_fails(self):
        shutil.rmtree(self.fx.cts)
        self.assertFails("expected exactly one OpenROAD.CTS step directory, found 0")

    def test_wrong_cts_root_fails(self):
        self.fx.save_cts(dict(psa.EXPECT_CTS, CTS_ROOT_BUFFER="sky130_fd_sc_hd__clkbuf_8"))
        self.assertFails("CTS CTS_ROOT_BUFFER")

    def test_cts_list_with_clkbuf_2_or_missing_fails(self):
        self.fx.save_cts(dict(psa.EXPECT_CTS, CTS_CLK_BUFFERS=["sky130_fd_sc_hd__clkbuf_8", "sky130_fd_sc_hd__clkbuf_4",
                                                               "sky130_fd_sc_hd__clkbuf_2"]))
        self.assertFails("CTS CTS_CLK_BUFFERS")
        self.fx.save_cts({"CTS_ROOT_BUFFER": psa.EXPECT_CTS["CTS_ROOT_BUFFER"]})
        self.assertFails("CTS CTS_CLK_BUFFERS is None")


if __name__ == "__main__":
    unittest.main()
