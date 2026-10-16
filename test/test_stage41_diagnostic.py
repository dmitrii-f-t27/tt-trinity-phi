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
import fetch_verify as fv  # noqa: E402
import summarize as s  # noqa: E402
import summarize_paired as sp  # noqa: E402

# Synthetic 32-name unannotated baseline shaped like the archived final reports (not a measurement)
BASELINE32 = (["uio_in[%d]" % i for i in (5, 6, 7)] + ["clkload%d/X" % i for i in range(12)]
              + ["tt_um_trinity_nano/HI"] + ["tt_um_trinity_nano_%d/HI" % i for i in range(151, 166)]
              + ["u_mul.u_sig.g_cpa\\[18\\].u_fa.u_fa_166/LO"])


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


def annot_text(un=BASELINE32, part=()):
    return "\n".join(["Found %d unannotated drivers." % len(un)] + [" " + n for n in un]
                     + ["Found %d partially unannotated drivers." % len(part)] + [" " + n for n in part] + [""])


def final_rpt(slew, fanout=()):
    """Synthetic archived final checks.rpt with the report_check_types region and the annotation section."""
    lines = ["=" * 20, " report_check_types -max_slew -max_cap -max_fanout -violators", "=" * 20,
             "======= max_ss_100C_1v60 Corner =======", "", "max slew", "", "Pin Limit Slew Slack", "----"]
    lines += ["%s  %.6f  %.6f  %.6f (VIOLATED)" % r for r in slew]
    if fanout:
        lines += ["", "max fanout", "", "Pin Limit Fanout Slack", "----"]
        lines += ["%s  %d  %d  %d (VIOLATED)" % r for r in fanout]
    lines += ["", "=" * 20, "report_parasitic_annotation -report_unannotated", "=" * 20, annot_text(), "=" * 20,
              "max slew violation count %d" % len(slew), "max fanout violation count %d" % len(fanout),
              "max cap violation count 0", "=" * 20]
    return "\n".join(lines)


SLEW = [("u1/A", 0.75, 0.873959, -0.123959), ("u2/B", 0.75, 0.80, -0.05)]


def paired_kind(mode, corner, kind, rows=()):
    return "\n".join(["PAIRED_KIND_BEGIN %s %s %s" % (mode, corner, kind), "max x", "----"]
                     + ["%s  %.6f  %.6f  %.6f (VIOLATED)" % r for r in rows]
                     + ["PAIRED_KIND_END %s %s %s" % (mode, corner, kind)])


def paired_kinds(mode, rows=None, skip=None):
    out = []
    for corner in c.CORNERS:
        for kind in c.KINDS:
            if skip in (corner, (corner, kind)):
                continue
            out.append(paired_kind(mode, corner, kind, (rows or {}).get(corner, {}).get(kind, ())))
    return "\n".join(out)


def archived(rows=None):
    return {corner: {k: list((rows or {}).get(corner, {}).get(k, ())) for k in c.KINDS} for corner in c.CORNERS}


class FinalRowsAndAnnotationTests(unittest.TestCase):
    def test_final_rows_per_kind_and_annotation_baseline(self):
        text = final_rpt(SLEW, fanout=[("u3/X", 10, 16, -6)])
        rows = c.parse_final_rows(text)
        self.assertEqual([r[0] for r in rows["slew"]], ["u1/A", "u2/B"])
        self.assertEqual(rows["fanout"], [("u3/X", 10.0, 16.0, -6.0)])
        self.assertEqual(rows["cap"], [])
        un, part = c.parse_annotation(text.split("report_parasitic_annotation -report_unannotated", 1)[1])
        self.assertEqual(un, set(BASELINE32))
        self.assertEqual(len(un), 32)
        self.assertEqual(part, set())

    def test_annotation_count_mismatch_fails(self):
        with self.assertRaises(ValueError):
            c.parse_annotation(annot_text().replace("Found 32 unannotated", "Found 31 unannotated"))
        with self.assertRaises(ValueError):
            c.parse_annotation("no headers here")


class PairedCalibrationTests(unittest.TestCase):
    def test_exact_reproduction_accepted(self):
        want = archived({"max_ss_100C_1v60": {"slew": SLEW}})
        got, probs = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": SLEW}}), "C2")
        self.assertEqual(probs, [])
        self.assertEqual(sp.calibrate(got, want), [])

    def test_wrong_pin_set_with_same_count_rejected(self):
        want = archived({"max_ss_100C_1v60": {"slew": SLEW}})
        other = [SLEW[0], ("u9/B", 0.75, 0.80, -0.05)]
        got, _ = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": other}}), "C2")
        probs = sp.calibrate(got, want)
        self.assertTrue(any("pin set differs (2 vs archived 2" in p for p in probs))

    def test_value_outside_tolerance_rejected(self):
        want = archived({"max_ss_100C_1v60": {"slew": SLEW}})
        near = [(SLEW[0][0], 0.75, SLEW[0][2] + 4e-7, SLEW[0][3]), SLEW[1]]
        far = [(SLEW[0][0], 0.75, SLEW[0][2] + 2e-6, SLEW[0][3]), SLEW[1]]
        got, _ = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": near}}), "C2")
        self.assertEqual(sp.calibrate(got, want), [])
        got, _ = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": far}}), "C2")
        self.assertTrue(sp.calibrate(got, want))

    def test_missing_corner_or_kind_fails(self):
        _, probs = sp.kind_blocks(paired_kinds("C2", skip="min_tt_025C_1v80"), "C2")
        self.assertEqual(sum("min_tt_025C_1v80" in p and "missing" in p for p in probs), 3)
        _, probs = sp.kind_blocks(paired_kinds("C2", skip=("nom_ss_100C_1v60", "cap")), "C2")
        self.assertTrue(any("nom_ss_100C_1v60: cap block missing" in p for p in probs))

    def test_duplicated_pin_rejected_even_with_same_count(self):
        want = archived({"max_ss_100C_1v60": {"slew": SLEW}})
        dup = [SLEW[0], SLEW[0]]
        got, _ = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": dup}}), "C2")
        self.assertTrue(any("C2 has a duplicated pin (2 rows, 1 unique)" in p for p in sp.calibrate(got, want)))
        got, _ = sp.kind_blocks(paired_kinds("C2", {"max_ss_100C_1v60": {"slew": SLEW}}), "C2")
        bad_archive = archived({"max_ss_100C_1v60": {"slew": SLEW + [SLEW[1]]}})
        self.assertTrue(any("archived has a duplicated pin" in p for p in sp.calibrate(got, bad_archive)))

    def test_c1_blocks_do_not_count_as_c2(self):
        _, probs = sp.kind_blocks(paired_kinds("C1"), "C2")
        self.assertEqual(len(probs), 27)

    def test_duplicate_block_fails(self):
        text = paired_kinds("C2") + "\n" + paired_kind("C2", "max_tt_025C_1v80", "slew")
        _, probs = sp.kind_blocks(text, "C2")
        self.assertTrue(any("duplicate" in p for p in probs))


def annot_log(per_corner):
    return "\n".join("PAIRED_ANNOT_BEGIN %s\n%sPAIRED_ANNOT_END %s" % (k, v, k) for k, v in per_corner.items())


class PairedAnnotationTests(unittest.TestCase):
    BASE = {corner: (set(BASELINE32), set()) for corner in c.CORNERS}

    def test_exact_32_name_baseline_accepted(self):
        out, probs = sp.annotation_blocks(annot_log({k: annot_text() for k in c.CORNERS}), self.BASE)
        self.assertEqual(probs, [])
        self.assertEqual(out["max_ss_100C_1v60"], {"unannotated": 32, "partial": 0})

    def test_new_unannotated_name_fails(self):
        logs = {k: annot_text() for k in c.CORNERS}
        logs["max_ss_100C_1v60"] = annot_text(BASELINE32 + ["fanout66/X"])
        _, probs = sp.annotation_blocks(annot_log(logs), self.BASE)
        self.assertTrue(any("max_ss_100C_1v60: unannotated set differs" in p for p in probs))

    def test_swapped_name_with_same_count_fails(self):
        logs = {k: annot_text() for k in c.CORNERS}
        logs["min_ss_100C_1v60"] = annot_text(BASELINE32[:-1] + ["fanout66/X"])
        _, probs = sp.annotation_blocks(annot_log(logs), self.BASE)
        hits = [p for p in probs if "min_ss_100C_1v60" in p and "unannotated set differs" in p]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].endswith("(32 vs 32)"))
        self.assertFalse(any("partially" in p for p in probs))

    def test_new_partial_binding_fails(self):
        logs = {k: annot_text() for k in c.CORNERS}
        logs["nom_tt_025C_1v80"] = annot_text(part=["fanout18/X"])
        _, probs = sp.annotation_blocks(annot_log(logs), self.BASE)
        self.assertTrue(any("partially unannotated set differs" in p for p in probs))

    def test_missing_corner_fails(self):
        logs = {k: annot_text() for k in c.CORNERS[:-1]}
        _, probs = sp.annotation_blocks(annot_log(logs), self.BASE)
        self.assertTrue(any("!= the 9 corners" in p for p in probs))


ENV = {"STAGE41_SPEF_NOM": "/r/nom.spef", "STAGE41_SPEF_MIN": "/r/min.spef", "STAGE41_SPEF_MAX": "/r/max.spef"}


def spef_log(override=None, body=""):
    out = []
    for corner in c.CORNERS:
        rc = corner.split("_")[0].upper()
        rc, path = (override or {}).get(corner, (rc, ENV["STAGE41_SPEF_" + rc]))
        out += ["PAIRED_SPEF_BEGIN %s %s %s" % (corner, rc, path), body, "PAIRED_SPEF_END %s" % corner]
    return "\n".join(out)


class PairedSpefTests(unittest.TestCase):
    def test_explicit_mapping_accepted(self):
        out, probs = sp.spef_blocks(spef_log(), ENV)
        self.assertEqual(probs, [])
        self.assertEqual(out["min_ss_100C_1v60"]["path"], "/r/min.spef")

    def test_wrong_corner_mapping_fails(self):
        _, probs = sp.spef_blocks(spef_log({"min_ss_100C_1v60": ("MIN", "/r/max.spef")}), ENV)
        self.assertTrue(any("SPEF min_ss_100C_1v60" in p for p in probs))
        _, probs = sp.spef_blocks(spef_log({"max_tt_025C_1v80": ("NOM", "/r/nom.spef")}), ENV)
        self.assertTrue(any("SPEF max_tt_025C_1v80" in p for p in probs))

    def test_reader_warning_fails(self):
        _, probs = sp.spef_blocks(spef_log(body="[WARNING ORD-2006] net n1 not found"), ENV)
        self.assertEqual(sum("reader warning" in p for p in probs), 9)


def good_prov(mode="paired"):
    h = {k: "%064x" % i for i, k in enumerate(["config", "state_in", "tcl", "records_tcl", "drivers", "odb",
                                                "derived_state"])}
    if mode == "paired":
        h.update({"spef_" + rc.lower(): v for rc, v in c.SPEF_SHA256.items()})
    return {"status": "PASS", "mode": mode, "hash_before": dict(h), "hash_after": dict(h),
            "derived_state": {"parent": "p", "kept": ["odb"], "dropped": ["spef"]}}


class ProvenanceTests(unittest.TestCase):
    def test_unchanged_sources_accepted(self):
        self.assertEqual(sp.check_provenance("paired", good_prov(), "paired"), [])
        self.assertEqual(sp.check_provenance("c3", good_prov("c3"), "c3"), [])

    def test_changed_source_fails(self):
        p = good_prov()
        p["hash_after"]["odb"] = "f" * 64
        self.assertTrue(any("odb changed" in x for x in sp.check_provenance("paired", p, "paired")))

    def test_missing_model_hash_or_wrong_spef_fails(self):
        p = good_prov()
        del p["hash_before"]["spef_max"]
        self.assertTrue(any("spef_max hash not recorded" in x for x in sp.check_provenance("paired", p, "paired")))
        p = good_prov()
        p["hash_before"]["spef_min"] = p["hash_after"]["spef_min"] = c.SPEF_SHA256["MAX"]
        self.assertTrue(any("MIN SPEF" in x for x in sp.check_provenance("paired", p, "paired")))

    def test_derived_state_must_keep_only_odb(self):
        p = good_prov()
        p["derived_state"]["kept"] = ["odb", "spef"]
        self.assertTrue(sp.check_provenance("paired", p, "paired"))

    def test_pinned_archive_files(self):
        used = dict(c.PINNED_STATES)
        for rc, h in c.SPEF_SHA256.items():
            used["runs/wokwi/55-openroad-rcx/%s/tt_um_trinity_nano.%s.spef" % (rc.lower(), rc.lower())] = h
        for corner, h in c.FINAL_CHECKS_SHA256.items():
            used[c.FINAL_STA_DIR + corner + "/checks.rpt"] = h
        self.assertEqual(fv.pinned_problems(used), [])
        used[c.STATE56_IN] = "0" * 64
        self.assertEqual(len(fv.pinned_problems(used)), 1)
        self.assertTrue(all(len(h) == 64 for h in list(c.FINAL_CHECKS_SHA256.values())
                            + list(c.SPEF_SHA256.values()) + list(c.PINNED_STATES.values())))


def probe_block(mode, corner, drv, rise="0.912345", total="0.030000", net=True):
    lines = ["PAIRED_PROBE_BEGIN %s %s %s" % (mode, corner, drv),
             "PAIRED_SLEW out rise %s" % rise, "PAIRED_SLEW out fall 0.900000",
             "PAIRED_SLEW in rise 0.100000", "PAIRED_SLEW in fall 0.100000"]
    if net:
        lines += ["Net net66", " Pin capacitance: 0.010000", " Wire capacitance: 0.020000-0.021000",
                  " Total capacitance: %s" % total, " Number of drivers: 1", " Number of loads: 10",
                  " Number of pins: 11"]
    else:
        lines += ["PAIRED_NET_UNKNOWN"]
    return "\n".join(lines + ["PAIRED_PROBE_END %s %s %s" % (mode, corner, drv)])


def probe_text(mode="C2", drv="fanout66/X", **special):
    return "\n".join(probe_block(mode, corner, drv, **(special if corner == "max_ss_100C_1v60" else {}))
                     for corner in c.CORNERS)


class ProbeTests(unittest.TestCase):
    def test_complete_probe_accepted(self):
        out, probs = sp.probes(probe_text(), "C2", ["fanout66/X"])
        self.assertEqual(probs, [])
        got = out[("max_ss_100C_1v60", "fanout66/X")]
        self.assertEqual(got["slew_raw"]["out_rise"], 0.912345)
        self.assertEqual(got["caps"], {"pin": "0.010000", "wire": "0.020000-0.021000", "total": "0.030000"})
        self.assertEqual(got["counts"], {"drivers": 1, "loads": 10, "pins": 11})

    def test_unknown_per_corner_slew_fails_and_is_not_zero(self):
        out, probs = sp.probes(probe_text(rise="UNKNOWN"), "C2", ["fanout66/X"])
        self.assertEqual(out[("max_ss_100C_1v60", "fanout66/X")]["slew_raw"]["out_rise"], "UNKNOWN")
        self.assertTrue(any("max_ss_100C_1v60" in p and "out_rise UNKNOWN" in p for p in probs))

    def test_malformed_or_missing_caps_fail(self):
        _, probs = sp.probes(probe_text(total="n/a"), "C2", ["fanout66/X"])
        self.assertTrue(any("total capacitance missing or malformed" in p for p in probs))
        _, probs = sp.probes(probe_text(net=False), "C2", ["fanout66/X"])
        self.assertTrue(any("exactly one net" in p for p in probs))
        self.assertTrue(any("pin capacitance missing" in p for p in probs))

    def test_missing_probe_fails(self):
        text = "\n".join(probe_block("C2", corner, "fanout66/X") for corner in c.CORNERS[1:])
        _, probs = sp.probes(text, "C2", ["fanout66/X"])
        self.assertTrue(any("missing" in p for p in probs))


class SlewUnitTests(unittest.TestCase):
    ROWS = {"C2": {"max_ss_100C_1v60": {"slew": [("fanout66/X", 0.75, 0.912345, -0.162345)]}}}

    def probe(self, rise, fall):
        return {"C2": {("max_ss_100C_1v60", "fanout66/X"): {"slew_raw": {"out_rise": rise, "out_fall": fall}}}}

    def test_unit_confirmed_only_by_report_rows(self):
        self.assertEqual(sp.slew_scale(self.probe(0.912345, 0.9), self.ROWS)[:2], ("report_units", 1))
        self.assertEqual(sp.slew_scale(self.probe(0.912345e-9, 0.9e-9), self.ROWS)[0], "seconds")

    def test_disagreeing_or_uncalibrated_unit_fails(self):
        scale, _, probs = sp.slew_scale(self.probe(0.95, 0.9), self.ROWS)
        self.assertIsNone(scale)
        self.assertTrue(probs)
        scale, n, probs = sp.slew_scale(self.probe(0.912345, 0.9), {"C2": {}})
        self.assertEqual((scale, n), (None, 0))
        self.assertTrue(probs)


class MetaTests(unittest.TestCase):
    def line(self, mode, drv, typ="CORE", clocks="NONE"):
        return ("PAIRED_META %s %s master=sky130_fd_sc_hd__clkbuf_1 type=%s inst_dont_touch=UNKNOWN net=net66 "
                "net_dont_touch=0 clocks=%s" % (mode, drv, typ, clocks))

    def test_unknown_clock_and_type_stay_unknown(self):
        text = "\n".join([self.line("C1", "fanout66/X", typ="UNKNOWN", clocks="UNKNOWN"),
                          self.line("C1", "fanout35/X")])
        out, probs = sp.parse_meta(text, "C1", ["fanout66/X", "fanout35/X"])
        self.assertEqual(probs, [])
        self.assertEqual(out["fanout66/X"]["clocks"], "UNKNOWN")
        self.assertEqual(out["fanout66/X"]["is_core_master"], "UNKNOWN")
        self.assertEqual(out["fanout66/X"]["inst_dont_touch"], "UNKNOWN")
        self.assertEqual(out["fanout35/X"]["clocks"], "NONE")
        self.assertEqual(out["fanout35/X"]["is_core_master"], "YES")

    def test_core_only_from_exact_type_and_missing_meta_fails(self):
        out, probs = sp.parse_meta(self.line("C3", "fanout18/X", typ="3"), "C3", ["fanout18/X", "fanout20/X"])
        self.assertEqual(out["fanout18/X"]["is_core_master"], "UNINTERPRETED")
        self.assertTrue(any("fanout20/X missing" in p for p in probs))
        out, probs = sp.parse_meta("PAIRED_META C3 fanout20/X ABSENT", "C3", ["fanout20/X"])
        self.assertEqual((out, probs), ({"fanout20/X": {"absent": True}}, []))


if __name__ == "__main__":
    unittest.main()
