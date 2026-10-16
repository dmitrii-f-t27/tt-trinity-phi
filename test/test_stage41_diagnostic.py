#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed checks of the stage-41 diagnostic helpers (sidecar 9b) on small synthetic inputs.

The fixtures only exercise the parsers and guards; they are not measurements. Standard library only.
Usage (repo root): python3 test/test_stage41_diagnostic.py
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "diagnostics", "stage41"))
import common as c  # noqa: E402
import summarize as s  # noqa: E402


def good_meta():
    return {"id": c.ARTIFACT_ID, "name": c.ARTIFACT_NAME, "size_in_bytes": c.ARTIFACT_SIZE, "expired": False,
            "workflow_run": {"id": c.RUN_ID, "head_sha": c.MEASURED_SHA}}


def kind_block(corner, kind, rows=()):
    lines = ["STAGE41_KIND_BEGIN %s %s" % (corner, kind), "max %s" % kind, "", "Pin  Limit  Value  Slack", "----"]
    lines += ["%s  0.750000  0.900000  -0.150000 (VIOLATED)" % p for p in rows]
    return "\n".join(lines + ["STAGE41_KIND_END %s %s" % (corner, kind)])


def full_log(have="0", mode="RECONSTRUCTED_GRT_IN_MEMORY", skip=None, extra=(), slew=None):
    out = ["STAGE41_HAVE_ROUTES " + have, "STAGE41_PARASITICS_MODE " + mode,
           "STAGE41_CORNERS 9 " + " ".join(c.CORNERS)]
    for corner in c.CORNERS:
        for kind in c.KINDS:
            if skip == (corner, kind):
                continue
            rows = (slew or {}).get(corner, ()) if kind == "slew" else ()
            out.append(kind_block(corner, kind, rows))
    return "\n".join(out + list(extra) + ["STAGE41_DONE"])


class MetadataTests(unittest.TestCase):
    def test_pinned_metadata_accepted(self):
        self.assertEqual(c.check_artifact_metadata(good_meta()), [])

    def test_wrong_head_sha_rejected(self):
        m = good_meta()
        m["workflow_run"]["head_sha"] = "0" * 40
        self.assertTrue(any("head_sha" in p for p in c.check_artifact_metadata(m)))

    def test_expired_or_resized_rejected(self):
        m = good_meta()
        m["expired"] = True
        m["size_in_bytes"] = 1
        probs = c.check_artifact_metadata(m)
        self.assertTrue(any("expired" in p for p in probs) and any("size_in_bytes" in p for p in probs))

    def test_instrumentation_sha_must_be_distinct_commit(self):
        self.assertIsNone(s.check_sha("a" * 40))
        self.assertIsNotNone(s.check_sha(None))
        self.assertIsNotNone(s.check_sha(c.MEASURED_SHA))


class ZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def make(self, names):
        path = os.path.join(self.tmp, "a.zip")
        with zipfile.ZipFile(path, "w") as z:
            for n in names:
                z.writestr(n, "x")
        return path

    def test_traversal_refused(self):
        with self.assertRaises(ValueError):
            c.safe_extract(self.make(["runs/ok.txt", "../evil.txt"]), os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_absolute_refused(self):
        with self.assertRaises(ValueError):
            c.safe_extract(self.make(["/etc/evil"]), os.path.join(self.tmp, "out"))


class LogTests(unittest.TestCase):
    def test_complete_log_parses_per_corner_and_kind(self):
        corners, have, mode, probs = s.parse_log(full_log(slew={"max_ss_100C_1v60": ["u1/X", "u2/A"]}))
        self.assertEqual(probs, [])
        self.assertEqual(len(corners["max_ss_100C_1v60"]["slew"]), 2)
        self.assertEqual(len(corners["nom_tt_025C_1v80"]["cap"]), 0)   # empty block with both markers

    def test_missing_kind_fails(self):
        _, _, _, probs = s.parse_log(full_log(skip=("max_ss_100C_1v60", "fanout")))
        self.assertTrue(any("fanout block missing" in p for p in probs))

    def test_duplicate_block_fails(self):
        _, _, _, probs = s.parse_log(full_log(extra=[kind_block("nom_tt_025C_1v80", "slew", ["u9/X"])]))
        self.assertTrue(any("duplicate" in p for p in probs))

    def test_wrong_mode_fails(self):
        _, _, _, probs = s.parse_log(full_log(have="1", mode="RECONSTRUCTED_GRT_IN_MEMORY"))
        self.assertTrue(any("inconsistent" in p for p in probs))

    def test_truncated_block_fails(self):
        text = full_log().replace("STAGE41_KIND_END min_ff_n40C_1v95 cap", "")
        _, _, _, probs = s.parse_log(text)
        self.assertTrue(any("not closed" in p for p in probs))

    def test_malformed_row_fails(self):
        text = full_log().replace("max slew", "max slew\nu9/X  0.75  oops (VIOLATED)", 1)
        _, _, _, probs = s.parse_log(text)
        self.assertTrue(any("malformed" in p for p in probs))


class RecordTests(unittest.TestCase):
    TSV = "\n".join([
        "N\ta/b.c\\[1\\]\tsky130_fd_sc_hd__buf_1\t10\t20\tR0\tPLACED",
        "N\t_100_\tsky130_fd_sc_hd__nand2_1\t30\t20\tMX\tPLACED",
        "I\tn1\ta/b.c\\[1\\]\tX\tOUTPUT\tsky130_fd_sc_hd__buf_1",
        "I\tn1\t_100_\tA\tINPUT\tsky130_fd_sc_hd__nand2_1",
        "I\tn2\t_100_\tY\tOUTPUT\tsky130_fd_sc_hd__nand2_1",
        "B\tn2\tuo_out[1]\tOUTPUT",
        "I\tn3\t_100_\tB\tINPUT\tsky130_fd_sc_hd__nand2_1",
        "B\tn3\tui_in[0]\tINPUT",
        "I\tn4\t_200_\tA\tINPUT\tsky130_fd_sc_hd__buf_1",
    ])

    def test_hierarchical_driver_from_io_type(self):
        _, pin_net, drv, masters = s.parse_records(self.TSV)
        out, probs = s.map_drivers(["_100_/A", "a/b.c\\[1\\]/X", "uo_out[1]", "_100_/B"], pin_net, drv, masters)
        self.assertEqual(probs, [])
        self.assertEqual(out["a/b.c[1]/X"], "sky130_fd_sc_hd__buf_1")
        self.assertEqual(out["_100_/Y"], "sky130_fd_sc_hd__nand2_1")
        self.assertEqual(out["ui_in[0]"], "top-level input port")

    def test_unknown_pin_and_driverless_net_fail(self):
        _, pin_net, drv, masters = s.parse_records(self.TSV)
        _, probs = s.map_drivers(["ghost/A", "_200_/A"], pin_net, drv, masters)
        self.assertTrue(any("not found" in p for p in probs) and any("0 drivers" in p for p in probs))

    def test_malformed_record_fails(self):
        with self.assertRaises(ValueError):
            s.parse_records("N\tonly_three\tfields")


class FinalCountTests(unittest.TestCase):
    RPT = "\n".join(["max slew", "", "Pin Limit Slew Slack", "----",
                     "u1/X  0.750000  0.900000  -0.150000 (VIOLATED)", "",
                     "max fanout", "", "u2/X  10  16  -6 (VIOLATED)",
                     "=====", "max slew violation count 1", "max fanout violation count 1",
                     "max cap violation count 0"])

    def test_footer_counts(self):
        self.assertEqual(c.parse_final_counts(self.RPT), {"slew": 1, "cap": 0, "fanout": 1})

    def test_footer_row_mismatch_and_missing_footer_fail(self):
        with self.assertRaises(ValueError):
            c.parse_final_counts(self.RPT.replace("max slew violation count 1", "max slew violation count 2"))
        with self.assertRaises(ValueError):
            c.parse_final_counts(self.RPT.replace("max cap violation count 0", ""))


if __name__ == "__main__":
    unittest.main()
