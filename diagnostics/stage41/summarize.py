#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Parse the stage-41 observation, map violators to their drivers from OpenDB, compare counts (sidecar 9b).

Fail closed (diagnostic FAIL, never COMPLETE): a corner or a kind block (slew / cap / fanout) missing,
duplicated or truncated; a malformed VIOLATED row; the corner list not equal to the 9 expected corners;
have_routes and the parasitics mode inconsistent; the design records (instances, masters, locations,
orientations, connectivity) different before and after the global-route step; a violating pin that is not
found in OpenDB or whose net has not exactly one driver; provenance reports not PASS; an instrumentation SHA
that is not 40 hex or equals the measured SHA; the archived final counts unreadable or different from the
reference. Counts are per corner and per kind, never aggregated. Drivers come from the stage-41 OpenDB only
(output ITerm, or input top-level BTerm); the final design is not mapped, so final drivers stay UNKNOWN.
Diagnostic COMPLETE only means the measurement is complete; the physical status of f6f4db6 stays FAIL.
Standard library only. Usage: python3 summarize.py --obs DIR --runs RUNS_DIR --fetch-report F --out DIR
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

MODES = {"1": "HISTORIC_ROUTES_FROM_ODB", "0": "RECONSTRUCTED_GRT_IN_MEMORY"}


def norm(name):
    return name.replace("\\", "")


def parse_log(text):
    """-> ({corner: {kind: rows}}, have_routes, mode, problems)."""
    problems = []
    hr = re.findall(r"^STAGE41_HAVE_ROUTES (\S+)\s*$", text, re.M)
    md = re.findall(r"^STAGE41_PARASITICS_MODE (\S+)\s*$", text, re.M)
    have_routes = hr[0] if len(hr) == 1 else None
    mode = md[0] if len(md) == 1 else None
    if have_routes not in MODES:
        problems.append("have_routes not reported exactly once as 0/1 (%r)" % hr)
    elif mode != MODES[have_routes]:
        problems.append("parasitics mode %r inconsistent with have_routes %s" % (mode, have_routes))
    cl = re.findall(r"^STAGE41_CORNERS (\d+) (.*?)\s*$", text, re.M)
    if len(cl) != 1 or int(cl[0][0]) != 9 or set(cl[0][1].split()) != set(c.CORNERS) \
            or len(cl[0][1].split()) != 9:
        problems.append("corner list %r != the 9 expected corners" % cl)
    if len(re.findall(r"^STAGE41_DONE\s*$", text, re.M)) != 1:
        problems.append("observation did not reach STAGE41_DONE exactly once")
    begins = re.findall(r"^STAGE41_KIND_BEGIN (\S+) (\S+)\s*$", text, re.M)
    ends = re.findall(r"^STAGE41_KIND_END (\S+) (\S+)\s*$", text, re.M)
    if len(begins) != len(set(begins)):
        problems.append("duplicate kind block(s)")
    if sorted(begins) != sorted(ends):
        problems.append("kind blocks not closed: %d begin / %d end" % (len(begins), len(ends)))
    corners = {}
    for corner, kind, body in re.findall(
            r"^STAGE41_KIND_BEGIN (\S+) (\S+)\s*\n(.*?)^STAGE41_KIND_END \1 \2\s*$", text, re.M | re.S):
        if "STAGE41_KIND_BEGIN" in body:
            problems.append("%s %s: nested or truncated block" % (corner, kind))
            continue
        try:
            corners.setdefault(corner, {})[kind] = c.parse_rows(body)
        except ValueError as e:
            problems.append("%s %s: %s" % (corner, kind, e))
    for corner in c.CORNERS:
        for kind in c.KINDS:
            if kind not in corners.get(corner, {}):
                problems.append("%s: %s block missing" % (corner, kind))
    if set(corners) - set(c.CORNERS):
        problems.append("unexpected corners %s" % sorted(set(corners) - set(c.CORNERS)))
    return corners, have_routes, mode, problems


def parse_records(text):
    """Design records TSV -> (sorted record list, {inst/mterm or port: net}, {net: [driver keys]}, masters)."""
    records, pin_net, drivers, masters = [], {}, {}, {}
    for line in text.splitlines():
        if not line:
            continue
        f = line.split("\t")
        records.append(tuple(f))
        if f[0] == "N" and len(f) == 7:
            masters[norm(f[1])] = f[2]
        elif f[0] == "I" and len(f) == 6:
            net, key = f[1], (norm(f[2]), f[3])
            pin_net[key] = net
            masters.setdefault(norm(f[2]), f[5])
            if f[4] == "OUTPUT":
                drivers.setdefault(net, []).append(key)
        elif f[0] == "B" and len(f) == 4:
            net, key = f[1], ("", norm(f[2]))
            pin_net[key] = net
            if f[3] == "INPUT":
                drivers.setdefault(net, []).append(key)
        else:
            raise ValueError("malformed design record: %r" % line)
    return sorted(records), pin_net, drivers, masters


def pin_key(pin):
    """'a/b.c\\[1\\]/X' -> ('a/b.c[1]', 'X') split at the LAST slash; a port has no slash."""
    pin = norm(pin)
    if "/" in pin:
        inst, term = pin.rsplit("/", 1)
        return inst, term
    return "", pin


def map_drivers(pins, pin_net, drivers, masters):
    """-> ({driver 'inst/term' or 'port': master}, problems). Unknown pin or not exactly one driver = problem."""
    out, problems = {}, []
    for pin in pins:
        net = pin_net.get(pin_key(pin))
        if net is None:
            problems.append("violating pin %s not found in OpenDB" % pin)
            continue
        drv = drivers.get(net, [])
        if len(drv) != 1:
            problems.append("violating pin %s: net %s has %d drivers (UNKNOWN)" % (pin, net, len(drv)))
            continue
        inst, term = drv[0]
        out["%s/%s" % (inst, term) if inst else term] = masters.get(inst) if inst else "top-level input port"
    return out, problems


def check_sha(sha):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return "instrumentation SHA %r is not a 40-hex commit" % sha
    if sha == c.MEASURED_SHA:
        return "instrumentation SHA equals the measured SHA"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", required=True)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--fetch-report", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    summary = {"measured_sha": c.MEASURED_SHA, "instrumentation_sha": os.environ.get("GITHUB_SHA"),
               "physical_status": "FAIL (f6f4db6 strict sign-off failed; this diagnostic does not change it)",
               "final_driver_mapping": "UNKNOWN (the final design is not mapped)",
               "problems": []}
    P = summary["problems"]
    err = check_sha(summary["instrumentation_sha"])
    if err:
        P.append(err)
    for name, path in (("fetch", args.fetch_report), ("measure", os.path.join(args.obs, "measure-provenance.json"))):
        try:
            rep = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            P.append("%s report missing or unreadable" % name)
            continue
        summary[name + "_status"] = rep.get("status")
        if rep.get("status") != "PASS":
            P.append("%s report status %r" % (name, rep.get("status")))
    corners = {}
    log = os.path.join(args.obs, "stage41-openroad.log")
    if os.path.isfile(log):
        corners, summary["have_routes"], summary["parasitics_mode"], probs = parse_log(
            open(log, encoding="utf-8", errors="replace").read())
        P += probs
    else:
        P.append("observation log missing")
    recs = {}
    for which in ("before", "after"):
        path = os.path.join(args.obs, "stage41-design-%s.tsv" % which)
        try:
            recs[which] = parse_records(open(path, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            P.append("design records %s unusable: %s" % (which, e))
    if len(recs) == 2:
        if recs["before"][0] != recs["after"][0]:
            P.append("design records differ before/after the global-route step")
        if not recs["after"][3]:
            P.append("design records contain no instance")
    final = {}
    for corner in c.CORNERS:
        path = os.path.join(args.runs, "wokwi", "56-openroad-stapostpnr", corner, "checks.rpt")
        try:
            final[corner] = c.parse_final_counts(open(path, encoding="utf-8", errors="replace").read())
        except (OSError, ValueError) as e:
            P.append("final %s: %s" % (corner, e))
            continue
        if final[corner]["slew"] != c.FINAL_SLEW_PINS.get(corner, 0):
            P.append("final %s slew %d != reference %d" % (corner, final[corner]["slew"],
                                                          c.FINAL_SLEW_PINS.get(corner, 0)))
    rows = {}
    for corner in c.CORNERS:
        kinds = corners.get(corner, {})
        if len(kinds) != 3:
            continue
        row = {"stage41": {k: len(kinds[k]) for k in c.KINDS}, "final": final.get(corner)}
        if "after" in recs:
            _, pin_net, drivers, masters = recs["after"]
            row["stage41_slew_drivers"], probs = map_drivers([r[0] for r in kinds["slew"]], pin_net, drivers,
                                                              masters)
            P += ["%s: %s" % (corner, p) for p in probs]
        rows[corner] = row
    summary["corners"] = rows
    summary["diagnostic_status"] = "COMPLETE" if not P else "FAIL"
    with open(os.path.join(args.out, "stage41-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    lines = ["# Stage-41 read-only observation of %s" % c.MEASURED_SHA, "",
             "Diagnostic: **%s**. Physical status: %s." % (summary["diagnostic_status"], summary["physical_status"]),
             "Parasitics: %s (have_routes=%s). Final drivers: %s. No causal claim." % (
                 summary.get("parasitics_mode"), summary.get("have_routes"), summary["final_driver_mapping"]), "",
             "| corner | stage-41 slew / cap / fanout pins | stage-41 slew drivers | final slew / cap / fanout |",
             "|---|---|---|---|"]
    for corner, row in rows.items():
        s, fin = row["stage41"], row["final"]
        lines.append("| %s | %d / %d / %d | %s | %s |" % (
            corner, s["slew"], s["cap"], s["fanout"], len(row.get("stage41_slew_drivers", {})),
            "%d / %d / %d" % (fin["slew"], fin["cap"], fin["fanout"]) if fin else "unavailable"))
    if P:
        lines += ["", "Problems:"] + ["- " + p for p in P]
    with open(os.path.join(args.out, "STAGE41_SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if not P else 1


if __name__ == "__main__":
    sys.exit(main())
