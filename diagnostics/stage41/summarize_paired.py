#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Summary of the paired same-final-design observation (sidecar sections 9c, 9d) and of C3.

Since 9d, C1 (global-route estimate, --paired) and C2 (SPEF only, cold, --c2) are SEPARATE processes with
separate logs; C2 is calibrated only from its own log. Fail closed also on: C1 / C2 output in the wrong log,
the C2 log not declaring SPEF_ONLY_COLD_PROCESS, C1 and C2 not distinct process instances, the final ODB,
config, states or helpers differing between them, or the records of C1 and C2 (before / after) differing.

Fail closed (diagnostic FAIL, no conclusion): any C1 or C2 corner/kind block missing, duplicated, unclosed or
malformed; C2 not reproducing the archived final reports exactly (violating pin set per corner and kind, and
limit / value / slack within 1e-6); the unannotated-driver set per corner not EXACTLY the archived one, or any
partially unannotated driver not in the archived (empty) set; a SPEF read block missing, mapped to the wrong
RC file, or containing a WARNING / ERROR line; logical and placement records changing between C1 and C2; the
C2 max_ss slew drivers (mapped through OpenDB) not equal to the committed 30-driver list; a duplicated row
pin; a probe block missing, a per-corner slew UNKNOWN, report_net without exactly one net or with missing /
malformed pin, wire or total capacitance or pin counts; the per-corner slew unit not reproducing every
report_check_types row of a probed driver; a driver metadata line missing (C1, C3); provenance not PASS;
instrumentation SHA not a distinct 40-hex commit. get_property slew_max_* is the worst over ALL corners and is
reported only with that scope. Unknown values stay UNKNOWN (no NONE / false fallback); clock role is the STA
clock set, never the cell name; "core master" only from the exact OpenDB type CORE. No causal claim.
Standard library only. Usage: python3 summarize_paired.py --paired DIR --c2 DIR --c3 DIR --c0 DIR --runs RUNS --fetch-report F
--out DIR
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import summarize as s  # noqa: E402

TOL = 1e-6
DRIVERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "final_slew_drivers.json")
SLEW_KEYS = ("out_rise", "out_fall", "in_rise", "in_fall")
CAP = re.compile(r"^\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?$")
# Candidate units of the raw Vertex.slew_corner value: report units (ns) or seconds. The unit is accepted only
# if it reproduces EVERY report_check_types slew row of a probed driver pin; it is never assumed.
SLEW_SCALES = {"report_units": 1.0, "seconds": 1e9}
META = re.compile(r"^PAIRED_META (\S+) (\S+) (?:(ABSENT)|master=(\S+) type=(\S+) inst_dont_touch=(\S+) net=(\S+) "
                  r"net_dont_touch=(\S+) clocks=(\S+))\s*$", re.M)


def num(v):
    try:
        return float(v)
    except ValueError:
        return "UNKNOWN"


def parse_meta(text, mode, drivers):
    """PAIRED_META lines of one mode -> ({driver: fields}, problems). Values are kept as printed: UNKNOWN stays
    UNKNOWN (never NONE / false). is_core_master is YES only for the exact master type CORE."""
    probs, out = [], {}
    for m in META.finditer(text):
        if m.group(1) != mode:
            continue
        drv = m.group(2)
        if drv in out:
            probs.append("%s meta %s duplicated" % (mode, drv))
        if m.group(3):
            out[drv] = {"absent": True}
            continue
        typ = m.group(5)
        out[drv] = {"master": m.group(4), "type": typ, "inst_dont_touch": m.group(6), "net": m.group(7),
                    "net_dont_touch": m.group(8), "clocks": m.group(9),
                    "is_core_master": "YES" if typ == "CORE" else ("UNKNOWN" if typ in ("UNKNOWN", "NONE")
                                                                    else "UNINTERPRETED")}
    for drv in drivers:
        if drv not in out:
            probs.append("%s meta %s missing" % (mode, drv))
    return out, probs


def slew_scale(probe_by_mode, rows_by_mode):
    """Calibrate the unit of the raw per-corner driver slews against the explicit report rows: for every probed
    driver output pin that is a slew violator row in the same mode and corner, max(rise, fall) * scale must equal
    the reported value within TOL. -> (scale name or None, comparisons, problems)."""
    pairs = []
    for mode, probe in probe_by_mode.items():
        for (corner, drv), p in probe.items():
            for row in rows_by_mode.get(mode, {}).get(corner, {}).get("slew", []):
                if s.norm(row[0]) == s.norm(drv):
                    r, f = p["slew_raw"].get("out_rise"), p["slew_raw"].get("out_fall")
                    if isinstance(r, float) and isinstance(f, float):
                        pairs.append((max(r, f), row[2]))
    if not pairs:
        return None, 0, ["per-corner slew unit not calibrated: no probed driver is a report row"]
    for name, k in SLEW_SCALES.items():
        if all(abs(raw * k - rep) <= TOL for raw, rep in pairs):
            return name, len(pairs), []
    return None, len(pairs), ["per-corner slew API disagrees with report_check_types in every candidate unit "
                              "(%d comparisons)" % len(pairs)]


def kind_blocks(text, mode):
    """-> ({corner: {kind: rows}}, problems) for the PAIRED_KIND blocks of one mode."""
    probs, out = [], {}
    begins = re.findall(r"^PAIRED_KIND_BEGIN %s (\S+) (\S+)\s*$" % mode, text, re.M)
    ends = re.findall(r"^PAIRED_KIND_END %s (\S+) (\S+)\s*$" % mode, text, re.M)
    if len(begins) != len(set(begins)):
        probs.append("%s: duplicate kind block(s)" % mode)
    if sorted(begins) != sorted(ends):
        probs.append("%s: kind blocks not closed (%d begin / %d end)" % (mode, len(begins), len(ends)))
    pat = r"^PAIRED_KIND_BEGIN %s (\S+) (\S+)\s*\n(.*?)^PAIRED_KIND_END %s \1 \2\s*$" % (mode, mode)
    for corner, kind, body in re.findall(pat, text, re.M | re.S):
        if "PAIRED_KIND_BEGIN" in body:
            probs.append("%s %s %s: nested or truncated block" % (mode, corner, kind))
            continue
        try:
            out.setdefault(corner, {})[kind] = c.parse_rows(body)
        except ValueError as e:
            probs.append("%s %s %s: %s" % (mode, corner, kind, e))
    for corner in c.CORNERS:
        for kind in c.KINDS:
            if kind not in out.get(corner, {}):
                probs.append("%s %s: %s block missing" % (mode, corner, kind))
    for corner in sorted(set(out) - set(c.CORNERS)):
        probs.append("%s: unexpected corner %s" % (mode, corner))
    for corner, kinds in out.items():
        for kind in sorted(set(kinds) - set(c.KINDS)):
            probs.append("%s %s: unexpected kind %s" % (mode, corner, kind))
    return out, probs


def calibrate(c2, archived):
    """C2 vs archived final rows: exact pin sets per corner and kind; numbers within TOL."""
    probs = []
    for corner in c.CORNERS:
        for kind in c.KINDS:
            rows_got = c2.get(corner, {}).get(kind, [])
            rows_want = archived.get(corner, {}).get(kind, [])
            got = {r[0]: r[1:] for r in rows_got}
            want = {r[0]: r[1:] for r in rows_want}
            dup = False
            for name, rows, uniq in (("C2", rows_got, got), ("archived", rows_want, want)):
                if len(rows) != len(uniq):
                    probs.append("calibration %s %s: %s has a duplicated pin (%d rows, %d unique)"
                                 % (corner, kind, name, len(rows), len(uniq)))
                    dup = True
            if dup:
                continue
            if set(got) != set(want):
                probs.append("calibration %s %s: pin set differs (%d vs archived %d; %d only C2, %d only archived)"
                             % (corner, kind, len(got), len(want), len(set(got) - set(want)),
                                len(set(want) - set(got))))
                continue
            for pin, vals in want.items():
                if any(abs(a - b) > TOL for a, b in zip(got[pin], vals)):
                    probs.append("calibration %s %s %s: %s != archived %s" % (corner, kind, pin, got[pin], vals))
    return probs


def annotation_blocks(text, archived):
    probs, out = [], {}
    found = re.findall(r"^PAIRED_ANNOT_BEGIN (\S+)\s*\n(.*?)^PAIRED_ANNOT_END \1\s*$", text, re.M | re.S)
    seen = [f[0] for f in found]
    if sorted(seen) != sorted(c.CORNERS):
        probs.append("annotation blocks %s != the 9 corners" % sorted(seen))
    for corner, body in found:
        try:
            un, part = c.parse_annotation(body)
        except ValueError as e:
            probs.append("annotation %s: %s" % (corner, e))
            continue
        out[corner] = {"unannotated": len(un), "partial": len(part)}
        want = archived.get(corner)
        if want is None:
            probs.append("annotation %s: no archived baseline" % corner)
            continue
        if un != want[0]:
            probs.append("annotation %s: unannotated set differs from the archived baseline (%d vs %d)"
                         % (corner, len(un), len(want[0])))
        if part != want[1]:
            probs.append("annotation %s: partially unannotated set differs (%d vs %d)"
                         % (corner, len(part), len(want[1])))
    return out, probs


def spef_blocks(text, env):
    probs, out = [], {}
    found = re.findall(r"^PAIRED_SPEF_BEGIN (\S+) (\S+) (\S+)\s*\n(.*?)^PAIRED_SPEF_END \1\s*$", text, re.M | re.S)
    if sorted(f[0] for f in found) != sorted(c.CORNERS):
        probs.append("SPEF read blocks %s != the 9 corners" % sorted(f[0] for f in found))
    for corner, rc, path, body in found:
        want_rc = corner.split("_")[0].upper()
        if rc != want_rc or path != env.get("STAGE41_SPEF_" + want_rc):
            probs.append("SPEF %s read %s (%s), expected the %s file" % (corner, rc, path, want_rc))
        bad = [l.strip() for l in body.splitlines() if "[WARNING" in l or "[ERROR" in l]
        if bad:
            probs.append("SPEF %s: %d reader warning/error line(s): %s" % (corner, len(bad), bad[:3]))
        out[corner] = {"rc": rc, "path": path}
    return out, probs


def probes(text, mode, drivers):
    probs, out = [], {}
    pat = r"^PAIRED_PROBE_BEGIN %s (\S+) (\S+)\s*\n(.*?)^PAIRED_PROBE_END %s \1 \2\s*$" % (mode, mode)
    for corner, drv, body in re.findall(pat, text, re.M | re.S):
        key = (corner, drv)
        if key in out:
            probs.append("%s probe %s %s duplicated" % (mode, corner, drv))
        where = "%s probe %s %s" % (mode, corner, drv)
        slews = {}
        for label, rf, v in re.findall(r"^PAIRED_SLEW (out|in) (rise|fall) (\S+)\s*$", body, re.M):
            if "%s_%s" % (label, rf) in slews:
                probs.append("%s: duplicated slew %s %s" % (where, label, rf))
            slews["%s_%s" % (label, rf)] = num(v)
        for k in SLEW_KEYS:
            if slews.get(k, "UNKNOWN") == "UNKNOWN":
                slews[k] = "UNKNOWN"
                probs.append("%s: per-corner slew %s UNKNOWN or malformed" % (where, k))
        net = re.findall(r"^Net (\S+)\s*$", body, re.M)
        caps = {}
        for name in ("Pin", "Wire", "Total"):
            found = re.findall(r"^ %s capacitance: (\S+)\s*$" % name, body, re.M)
            caps[name.lower()] = found[0] if len(found) == 1 and CAP.match(found[0]) else "UNKNOWN"
            if caps[name.lower()] == "UNKNOWN":
                probs.append("%s: %s capacitance missing or malformed" % (where, name.lower()))
        counts = {}
        for name in ("drivers", "loads", "pins"):
            found = re.findall(r"^ Number of %s: (\d+)\s*$" % name, body, re.M)
            counts[name] = int(found[0]) if len(found) == 1 else "UNKNOWN"
            if counts[name] == "UNKNOWN":
                probs.append("%s: number of %s missing" % (where, name))
        if len(net) != 1 or "PAIRED_NET_UNKNOWN" in body:
            probs.append("%s: report_net did not report exactly one net" % where)
        out[key] = {"slew_raw": slews, "net": net[0] if len(net) == 1 else "UNKNOWN", "caps": caps,
                    "counts": counts}
    for corner in c.CORNERS:
        for drv in drivers:
            if (corner, drv) not in out:
                probs.append("%s probe %s %s missing" % (mode, corner, drv))
    return out, probs


def check_provenance(name, prov, mode):
    """Measure provenance of one mode: PASS, the mode, every source hash recorded and unchanged before/after,
    the derived state keeping only "odb", and (paired) the 3 SPEF hashes equal to the pinned files."""
    probs = []
    if prov.get("status") != "PASS":
        probs.append("%s provenance status %r" % (name, prov.get("status")))
    if prov.get("mode") != mode:
        probs.append("%s provenance mode %r != %r" % (name, prov.get("mode"), mode))
    before, after = prov.get("hash_before") or {}, prov.get("hash_after") or {}
    need = ["config", "state_in", "tcl", "records_tcl", "drivers", "odb", "derived_state"]
    if mode == "c2":
        need += ["spef_nom", "spef_min", "spef_max"]
    for key in need:
        if not before.get(key):
            probs.append("%s source %s hash not recorded" % (name, key))
        elif before.get(key) != after.get(key):
            probs.append("%s source %s changed during the observation" % (name, key))
    if (prov.get("derived_state") or {}).get("kept") != ["odb"]:
        probs.append("%s derived state does not keep only odb" % name)
    if mode == "c2":
        for rc, h in c.SPEF_SHA256.items():
            if before.get("spef_" + rc.lower()) != h:
                probs.append("%s %s SPEF hash is not the pinned final %s file" % (name, rc, rc))
    return probs


SHARED_SOURCES = ("config", "state_in", "odb", "derived_state", "records_tcl", "drivers")


def check_processes(p1, p2):
    """C1 (paired) and C2 provenance (sidecar 9d): distinct process instances on the same final ODB, config,
    parent state, derived state and helpers (hashes before AND after equal across the two processes)."""
    probs = []
    i1, i2 = (p1.get("process") or {}).get("instance"), (p2.get("process") or {}).get("instance")
    if not i1 or not i2 or i1 == i2:
        probs.append("C1 and C2 are not distinct recorded process instances (%r, %r)" % (i1, i2))
    for key in SHARED_SOURCES:
        vals = [(p.get(h) or {}).get(key) for p in (p1, p2) for h in ("hash_before", "hash_after")]
        if not vals[0] or len(set(vals)) != 1:
            probs.append("source %s differs or is missing between the C1 and C2 processes" % key)
    return probs


C2_ONLY = re.compile(r"^(PAIRED_KIND_BEGIN C2|PAIRED_SPEF_BEGIN|PAIRED_ANNOT_BEGIN|PAIRED_PROBE_BEGIN C2|"
                     r"PAIRED_META C2|PAIRED_C2_MODEL|C2_DONE)\b", re.M)
C1_ONLY = re.compile(r"^(PAIRED_KIND_BEGIN C1|PAIRED_PROBE_BEGIN C1|PAIRED_META C1|PAIRED_HAVE_ROUTES|"
                     r"PAIRED_C1_MODE|PAIRED_DONE)\b", re.M)


def check_logs(c1_text, c2_text):
    """Routing of the two separate logs: no C2 output in the C1 log, no C1 / global-route output in the C2
    log, and the C2 log declares the SPEF-only cold model exactly once and completes exactly once."""
    probs = []
    if C2_ONLY.search(c1_text):
        probs.append("C1 log contains C2 / SPEF output (not a separate process)")
    if C1_ONLY.search(c2_text):
        probs.append("C2 log contains C1 / global-route output (not a cold SPEF-only process)")
    if len(re.findall(r"^PAIRED_C2_MODEL SPEF_ONLY_COLD_PROCESS\s*$", c2_text, re.M)) != 1:
        probs.append("C2 log does not declare PAIRED_C2_MODEL SPEF_ONLY_COLD_PROCESS exactly once")
    if len(re.findall(r"^C2_DONE\s*$", c2_text, re.M)) != 1:
        probs.append("C2 observation did not reach C2_DONE exactly once")
    return probs


def same_geometry(recs, names):
    """Logical and placement records of C1 and C2, before and after each process, must all be present and
    identical (the geometry is never changed)."""
    missing = [n for n in names if n not in recs]
    if missing:
        return ["records %s missing: geometry identity across C1 and C2 not shown" % missing]
    if len({tuple(recs[n][0]) for n in names}) != 1:
        return ["logical/placement records differ between C1 and C2 (before/after)"]
    return []


def corner_list(text, name):
    cl = re.findall(r"^PAIRED_CORNERS (\d+) (.*?)\s*$", text, re.M)
    if len(cl) != 1 or set(cl[0][1].split()) != set(c.CORNERS) or len(cl[0][1].split()) != 9:
        return ["%s corner list %r != the 9 expected corners" % (name, cl)]
    return []


def archived_final(runs):
    rows, annot, probs = {}, {}, []
    for corner in c.CORNERS:
        path = os.path.join(runs, "wokwi", "56-openroad-stapostpnr", corner, "checks.rpt")
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            probs.append("archived final %s missing" % corner)
            continue
        if c.sha256_file(path) != c.FINAL_CHECKS_SHA256[corner]:
            probs.append("archived final %s sha256 differs from the pinned value" % corner)
        try:
            rows[corner] = c.parse_final_rows(text)
            want = {"slew": c.FINAL_SLEW_PINS.get(corner, 0), "cap": 0, "fanout": 0}
            got = {k: len(v) for k, v in rows[corner].items()}
            if got != want:
                probs.append("archived final %s counts %s != reference %s" % (corner, got, want))
            m = re.search(r"report_parasitic_annotation -report_unannotated\s*\n(.*)", text, re.S)
            annot[corner] = c.parse_annotation(m.group(1) if m else "")
        except ValueError as e:
            probs.append("archived final %s: %s" % (corner, e))
    return rows, annot, probs


def main():
    ap = argparse.ArgumentParser()
    for a in ("--paired", "--c2", "--c3", "--c0", "--runs", "--fetch-report", "--out"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    drivers = json.load(open(DRIVERS_FILE, encoding="utf-8"))
    summary = {"measured_sha": c.MEASURED_SHA, "instrumentation_sha": os.environ.get("GITHUB_SHA"),
               "physical_status": "FAIL (f6f4db6 strict sign-off failed; this diagnostic does not change it)",
               "problems": []}
    P = summary["problems"]
    err = s.check_sha(summary["instrumentation_sha"])
    if err:
        P.append(err)
    prov = {}
    for name, path in (("fetch", args.fetch_report), ("paired", os.path.join(args.paired, "measure-provenance.json")),
                       ("c2", os.path.join(args.c2, "measure-provenance.json")),
                       ("c3", os.path.join(args.c3, "measure-provenance.json"))):
        try:
            prov[name] = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            P.append("%s provenance missing or unreadable" % name)
            prov[name] = {}
        if name == "fetch":
            if prov[name].get("status") != "PASS":
                P.append("fetch report status %r" % prov[name].get("status"))
        else:
            P += check_provenance(name, prov[name], name)
    summary["provenance"] = {k: {"status": v.get("status"), "hash_before": v.get("hash_before"),
                                 "hash_after": v.get("hash_after"), "derived_state": v.get("derived_state"),
                                 "librelane_version": v.get("librelane_version"), "process": v.get("process")}
                             for k, v in prov.items()}
    summary["used_files"] = prov["fetch"].get("used_files")
    P += check_processes(prov["paired"], prov["c2"])
    archived_rows, archived_annot, probs = archived_final(args.runs)
    P += probs

    logs = {}
    for name, d in (("C1", args.paired), ("C2", args.c2)):
        try:
            logs[name] = open(os.path.join(d, "stage41-openroad.log"), encoding="utf-8", errors="replace").read()
        except OSError:
            logs[name] = ""
            P.append("%s log missing" % name)
        P += corner_list(logs[name], name)
    text, text2 = logs["C1"], logs["C2"]
    P += check_logs(text, text2)
    summary["c2_model"] = "SPEF_ONLY_COLD_PROCESS"
    hr = re.findall(r"^PAIRED_HAVE_ROUTES (\S+)\s*$", text, re.M)
    md = re.findall(r"^PAIRED_C1_MODE (\S+)\s*$", text, re.M)
    summary["c1_have_routes"], summary["c1_mode"] = (hr[0] if len(hr) == 1 else None), (md[0] if len(md) == 1 else None)
    if summary["c1_have_routes"] not in s.MODES or summary["c1_mode"] != s.MODES.get(summary["c1_have_routes"]):
        P.append("C1 have_routes %r / mode %r inconsistent" % (hr, md))
    if len(re.findall(r"^PAIRED_DONE\s*$", text, re.M)) != 1:
        P.append("paired observation did not reach PAIRED_DONE exactly once")
    c1, probs = kind_blocks(text, "C1")
    P += probs
    # C2 only from the separate cold-process SPEF log
    c2, probs = kind_blocks(text2, "C2")
    P += probs
    P += calibrate(c2, archived_rows)
    summary["annotation"], probs = annotation_blocks(text2, archived_annot)
    P += probs
    summary["spef"], probs = spef_blocks(text2, prov["c2"].get("extra_env", {}))
    P += probs
    probe = {}
    for mode, log in (("C1", text), ("C2", text2)):
        probe[mode], probs = probes(log, mode, drivers["final_max_ss_drivers"])
        P += probs
    scale, n, probs = slew_scale(probe, {"C1": c1, "C2": c2})
    P += probs
    summary["per_corner_slew_unit"] = {"unit": scale or "UNCALIBRATED", "comparisons": n,
                                       "api": "Pin.vertices -> Vertex.slew_corner(rf, sta::find_corner, max)"}
    for mode in probe:
        for p in probe[mode].values():
            p["slew_ns"] = {k: (v * SLEW_SCALES[scale] if scale and isinstance(v, float) else "UNKNOWN")
                            for k, v in p["slew_raw"].items()}
    summary["meta_c1"], probs = parse_meta(text, "C1", drivers["final_max_ss_drivers"])
    P += probs
    summary["meta_c2"], probs = parse_meta(text2, "C2", drivers["final_max_ss_drivers"])
    P += probs
    summary["global_worst_all_corners"] = {
        "scope": "GLOBAL_WORST_ACROSS_ALL_CORNERS (get_property slew_max_*; not per corner)",
        "values": ["%s %s %s %s %s" % g for g in re.findall(
            r"^PAIRED_GLOBAL_WORST_ALL_CORNERS (\S+) (\S+) (\S+) (\S+) (\S+)\s*$", text, re.M)]}

    recs = {}
    for name, d in (("paired-c1-before", args.paired), ("paired-c1-after", args.paired),
                    ("c2-before", args.c2), ("c2-after", args.c2)):
        try:
            recs[name] = s.parse_records(open(os.path.join(d, name + ".tsv"), encoding="utf-8").read())
        except (OSError, ValueError) as e:
            P.append("records %s unusable: %s" % (name, e))
    P += same_geometry(recs, ("paired-c1-before", "paired-c1-after", "c2-before", "c2-after"))
    if "c2-after" in recs and "max_ss_100C_1v60" in c2:
        _, pin_net, drv, masters = recs["c2-after"]
        mapped, probs = s.map_drivers([r[0] for r in c2["max_ss_100C_1v60"]["slew"]], pin_net, drv, masters)
        P += ["C2 max_ss: " + p for p in probs]
        if set(mapped) != set(drivers["final_max_ss_drivers"]):
            P.append("C2 max_ss slew drivers %d != committed list %d" % (len(mapped), len(drivers["final_max_ss_drivers"])))
        summary["c2_max_ss_driver_masters"] = mapped

    # C3: stage-39 masters vs stage-41 masters of the 8 drivers
    summary["c3"] = {}
    try:
        c3text = open(os.path.join(args.c3, "stage41-openroad.log"), encoding="utf-8", errors="replace").read()
        _, _, _, m41 = s.parse_records(open(os.path.join(args.c0, "stage41-design-after.tsv"), encoding="utf-8").read())
        _, pn39, _, m39 = s.parse_records(open(os.path.join(args.c3, "c3-design.tsv"), encoding="utf-8").read())
    except (OSError, ValueError) as e:
        c3text, m41, pn39, m39 = "", {}, {}, {}
        P.append("C3 inputs unusable: %s" % e)
    if len(re.findall(r"^C3_DONE\s*$", c3text, re.M)) != 1:
        P.append("C3 did not reach C3_DONE exactly once")
    if not m39:
        P.append("C3 stage-39 records contain no instance")
    meta3, probs = parse_meta(c3text, "C3", drivers["stage41_drivers"])
    P += probs
    for drv in drivers["stage41_drivers"]:
        inst = drv.rsplit("/", 1)[0]
        meta = meta3.get(drv)
        if meta is None:
            continue
        if meta.get("absent"):
            # an observation, not a failure, when the records agree: the instance does not exist before stage 41
            if m39 and inst in m39:
                P.append("C3 driver %s reported ABSENT but is in the stage-39 records" % inst)
            summary["c3"][inst] = {"master_stage39": "ABSENT", "master_stage41": m41.get(inst, "UNKNOWN")}
            continue
        net = pn39.get(s.pin_key(drv))
        loads = sorted("%s/%s" % k if k[0] else k[1] for k, n in pn39.items() if n == net and k != s.pin_key(drv))
        summary["c3"][inst] = dict(meta, master_stage39=meta["master"], master_stage41=m41.get(inst, "UNKNOWN"),
                                   net_stage39=net if net is not None else "UNKNOWN",
                                   loads_stage39=loads if net is not None else "UNKNOWN")

    table = {}
    for corner in c.CORNERS:
        table[corner] = {"C1": {k: len(c1.get(corner, {}).get(k, [])) for k in c.KINDS},
                         "C2": {k: len(c2.get(corner, {}).get(k, [])) for k in c.KINDS},
                         "archived": {k: len(archived_rows.get(corner, {}).get(k, [])) for k in c.KINDS}}
    summary["counts"] = table
    summary["probes"] = {m: {"%s %s" % k: v for k, v in probe[m].items()} for m in probe}
    summary["diagnostic_status"] = "COMPLETE" if not P else "FAIL"
    with open(os.path.join(args.out, "paired-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    md = ["# Paired same-final-design observation (sidecar 9c) of %s" % c.MEASURED_SHA, "",
          "Diagnostic: **%s**. Physical status: %s. C1 parasitics: %s. No causal claim." % (
              summary["diagnostic_status"], summary["physical_status"], summary["c1_mode"]), "",
          "| corner | C1 GRT slew/cap/fanout | C2 cold SPEF slew/cap/fanout | archived final |", "|---|---|---|---|"]
    for corner, row in table.items():
        md.append("| %s | %s | %s | %s |" % (corner, *("%d / %d / %d" % tuple(row[k][x] for x in c.KINDS)
                                                       for k in ("C1", "C2", "archived"))))
    if P:
        md += ["", "Problems:"] + ["- " + p for p in P]
    with open(os.path.join(args.out, "PAIRED_SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if not P else 1


if __name__ == "__main__":
    sys.exit(main())
