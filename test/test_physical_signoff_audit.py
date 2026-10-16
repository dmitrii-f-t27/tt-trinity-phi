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
        write(os.path.join(self.run, "final", "nl", "top.nl.v"), "sky130_fd_sc_hd__buf_2 u1 (.A(a), .X(x));\n")

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
        self.assertEqual(rep["excluded_cell_hits"], {})   # buf_2 matches none of the delay-family patterns

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


if __name__ == "__main__":
    unittest.main()
