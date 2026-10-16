#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed physical sign-off audit of one LibreLane run (policy: sidecar section 9 of
specs/physical/gf16_dot4_sky26d_1x2_25mhz_eval.t27).

It READS the run directory of the official flow (default runs/wokwi); it never edits it and runs no tool.
Step directories are found by the `meta.step` of their own config.json, not by number. PASS needs ALL of:
  * policy constants of the sidecar: SIGNOFF_STA_CORNERS 9, SIGNOFF_MAX_SLEW_PS 750, SIGNOFF_MAX_FANOUT 10,
    SIGNOFF_ALLOWED_VIOLATIONS 0 (numbers of the policy only; they prove nothing about the design);
  * exactly one OpenROAD.STAPostPNR step; its config: CLOCK_PERIOD 40 (25 MHz), DIE_AREA of the 1x2 template,
    MAX_TRANSITION_CONSTRAINT 0.75, MAX_FANOUT_CONSTRAINT 10, STA_CORNERS == the nine expected corners,
    EXTRA_EXCLUDED_CELLS == the three HD delay-family patterns plus exactly buf_1, clkbuf_1 and clkbuf_2
    (section 9e) and exactly nor2_1 and nor2_2 (section 9f) (also in the Yosys.Synthesis config);
  * exactly one OpenROAD.CTS step; its config: CTS_CLK_BUFFERS == [clkbuf_8, clkbuf_4] and
    CTS_ROOT_BUFFER == clkbuf_16 (section 9e);
  * the checker steps accepted the strict corner wildcards: setup, hold, max slew and max cap all ["*"];
  * for EACH of the nine corners: <corner>/checks.rpt exists; metrics present, finite, numeric; setup and hold
    worst slack >= 0; setup/hold violation counts 0; max cap, max slew and max fanout violation counts 0 (the
    clock network is included, nothing is waived); the footer counts of checks.rpt equal the metrics;
  * final/metrics.json: Magic DRC 0, LVS errors 0, antenna 0, utilization <= 1; critical disconnected pins and
    filtered unannotated nets 0 where reported;
  * the final netlist exists, has HD cells, and no cell-name token matches an exclusion pattern
    (fnmatchcase on the real tokens; the configured pattern list alone proves nothing).
A missing, malformed, non-finite, negative or inconsistent value is a FAIL, never a zero. A missing final/
(e.g. after a deferred checker error) is reported as such, with whatever STA numbers exist, and FAILS.
Outputs: <out>/physical-signoff.json and <out>/PHYSICAL_SIGNOFF.md. Exit 0 only on PASS.
Usage (repo root): python3 test/physical_signoff_audit.py [--run-dir runs/wokwi] [--out physical-signoff]
"""
import argparse
import fnmatch
import glob
import hashlib
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIDECAR = os.path.join("specs", "physical", "gf16_dot4_sky26d_1x2_25mhz_eval.t27")

CORNERS = ["%s_%s" % (rc, pvt) for rc in ("nom", "min", "max")
           for pvt in ("tt_025C_1v80", "ss_100C_1v60", "ff_n40C_1v95")]
# exclusion PATTERNS of sidecar sections 9, 9e and 9f (fnmatch syntax, as LibreLane applies
# EXTRA_EXCLUDED_CELLS): the three delay families, the exact buffer drive floor and the exact NOR2 drive floor
# (buf_12 / clkbuf_16 / nor2b_1 do not match the exact names)
EXCLUDED = ["sky130_fd_sc_hd__clkdlybuf*", "sky130_fd_sc_hd__dlygate*", "sky130_fd_sc_hd__dlymetal*",
            "sky130_fd_sc_hd__buf_1", "sky130_fd_sc_hd__clkbuf_1", "sky130_fd_sc_hd__clkbuf_2",
            "sky130_fd_sc_hd__nor2_1", "sky130_fd_sc_hd__nor2_2"]
# CTS buffers of section 9e, read from the OpenROAD.CTS step config (the STAPostPNR config has no CTS keys)
EXPECT_CTS = {"CTS_CLK_BUFFERS": ["sky130_fd_sc_hd__clkbuf_8", "sky130_fd_sc_hd__clkbuf_4"],
              "CTS_ROOT_BUFFER": "sky130_fd_sc_hd__clkbuf_16"}
POLICY = {"SIGNOFF_STA_CORNERS": 9, "SIGNOFF_MAX_SLEW_PS": 750, "SIGNOFF_MAX_FANOUT": 10,
          "SIGNOFF_ALLOWED_VIOLATIONS": 0}
EXPECT_CFG = {"CLOCK_PERIOD": 40, "DIE_AREA": [0, 0, 161, 225.76],
              "MAX_TRANSITION_CONSTRAINT": 0.75, "MAX_FANOUT_CONSTRAINT": 10}
# checker step -> (its own corner variable, default variable it falls back to)
CHECKERS = {"Checker.SetupViolations": "SETUP_VIOLATION_CORNERS",
            "Checker.HoldViolations": "HOLD_VIOLATION_CORNERS",
            "Checker.MaxSlewViolations": "MAX_SLEW_VIOLATION_CORNERS",
            "Checker.MaxCapViolations": "MAX_CAP_VIOLATION_CORNERS"}
# per-corner metric -> report footer key (None = no footer line)
CORNER_COUNTS = {"timing__setup_vio__count": None, "timing__hold_vio__count": None,
                 "design__max_slew_violation__count": "max slew violation count",
                 "design__max_fanout_violation__count": "max fanout violation count",
                 "design__max_cap_violation__count": "max cap violation count"}
CORNER_SLACKS = ("timing__setup__ws", "timing__hold__ws")
FINAL_ZERO = ("magic__drc_error__count", "design__lvs_error__count", "route__antenna_violation__count")
FINAL_ZERO_IF_PRESENT = ("antenna__violating__nets", "antenna__violating__pins",
                         "design__critical_disconnected_pin__count", "timing__unannotated_net_filtered__count")


def _no_const(name):
    raise ValueError("non-finite JSON constant %s" % name)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f, parse_constant=_no_const)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def number(v):
    """A finite real number (not a bool), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


class Audit:
    def __init__(self, run_dir, root):
        self.run_dir, self.root = run_dir, root
        self.problems, self.sources, self._index = [], {}, None
        self.report = {"run_dir": run_dir, "git_sha": os.environ.get("GITHUB_SHA"), "corners": {}}

    def fail(self, msg):
        self.problems.append(msg)

    def read(self, path, what):
        """JSON file -> dict, recorded with its sha256; None (and a problem) when absent or malformed."""
        if not os.path.isfile(path):
            self.fail("%s missing: %s" % (what, path))
            return None
        try:
            data = load_json(path)
        except (OSError, ValueError) as e:
            self.fail("%s unreadable: %s (%s)" % (what, path, e))
            return None
        if not isinstance(data, dict):
            self.fail("%s is not a JSON object: %s" % (what, path))
            return None
        self.sources[os.path.relpath(path, self.run_dir)] = sha256(path)
        return data

    def steps(self, step_id):
        """Step directories whose config.json says meta.step == step_id. The run directory is indexed once;
        an unreadable config or one without a meta object is a problem, never silently skipped."""
        if self._index is None:
            self._index = []
            for cfg in sorted(glob.glob(os.path.join(self.run_dir, "*", "config.json"))):
                try:
                    data = load_json(cfg)
                except (OSError, ValueError) as e:
                    self.fail("step config unreadable: %s (%s)" % (cfg, e))
                    continue
                meta = data.get("meta") if isinstance(data, dict) else None
                if not isinstance(meta, dict):
                    self.fail("step config has no meta object: %s" % cfg)
                    continue
                self._index.append((meta.get("step"), os.path.dirname(cfg)))
        return [d for s, d in self._index if s == step_id]

    def one_step(self, step_id):
        found = self.steps(step_id)
        if len(found) != 1:
            self.fail("expected exactly one %s step directory, found %d: %s"
                      % (step_id, len(found), [os.path.basename(d) for d in found]))
            return None
        return found[0]

    def policy(self):
        path = os.path.join(self.root, SIDECAR)
        if not os.path.isfile(path):
            self.fail("sidecar missing: %s" % path)
            return
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.sources[SIDECAR] = sha256(path)
        got = {k: int(v) for k, v in re.findall(r"^pub const (SIGNOFF_\w+)\s*:\s*u32\s*=\s*(\d+)\s*;", text, re.M)}
        self.report["policy"] = got
        if got != POLICY:
            self.fail("sidecar SIGNOFF constants %r != %r" % (got, POLICY))

    def config(self, sta_dir):
        cfg = self.read(os.path.join(sta_dir, "config.json"), "STAPostPNR config")
        if cfg is None:
            return
        self.report["config"] = {k: cfg.get(k) for k in list(EXPECT_CFG) + ["STA_CORNERS", "EXTRA_EXCLUDED_CELLS"]}
        for key, want in EXPECT_CFG.items():
            got = cfg.get(key)
            ok = (isinstance(want, list) and isinstance(got, list) and len(got) == len(want)
                  and all(number(g) is not None and g == w for g, w in zip(got, want))) \
                or (not isinstance(want, list) and number(got) is not None and got == want)
            if not ok:
                self.fail("config %s is %r, expected %r" % (key, got, want))
        if cfg.get("STA_CORNERS") != CORNERS:
            self.fail("config STA_CORNERS %r != the nine expected corners %r" % (cfg.get("STA_CORNERS"), CORNERS))
        if cfg.get("EXTRA_EXCLUDED_CELLS") != EXCLUDED:
            self.fail("PnR EXTRA_EXCLUDED_CELLS %r != %r" % (cfg.get("EXTRA_EXCLUDED_CELLS"), EXCLUDED))
        syn = self.one_step("Yosys.Synthesis")
        if syn:
            scfg = self.read(os.path.join(syn, "config.json"), "Yosys.Synthesis config")
            if scfg is not None and scfg.get("EXTRA_EXCLUDED_CELLS") != EXCLUDED:
                self.fail("synthesis EXTRA_EXCLUDED_CELLS %r != %r" % (scfg.get("EXTRA_EXCLUDED_CELLS"), EXCLUDED))

    def cts(self):
        """Exactly one OpenROAD.CTS step; its config must carry the exact section-9e buffer list and root."""
        d = self.one_step("OpenROAD.CTS")
        if d is None:
            return
        cfg = self.read(os.path.join(d, "config.json"), "OpenROAD.CTS config")
        if cfg is None:
            return
        self.report["cts"] = {k: cfg.get(k) for k in EXPECT_CTS}
        for key, want in EXPECT_CTS.items():
            if cfg.get(key) != want:
                self.fail("CTS %s is %r, expected %r" % (key, cfg.get(key), want))

    def checkers(self):
        self.report["checker_corners"] = {}
        for step_id, var in CHECKERS.items():
            d = self.one_step(step_id)
            if d is None:
                continue
            cfg = self.read(os.path.join(d, "config.json"), step_id + " config")
            if cfg is None:
                continue
            wild = cfg.get(var) or cfg.get("TIMING_VIOLATION_CORNERS")
            effective = [w for w in wild if w != ""] if isinstance(wild, list) else None
            self.report["checker_corners"][step_id] = effective
            if effective != ["*"]:
                self.fail("%s checks corners %r (from %s / TIMING_VIOLATION_CORNERS), expected ['*']"
                          % (step_id, wild, var))

    def corners(self, sta_dir):
        state = self.read(os.path.join(sta_dir, "state_out.json"), "STAPostPNR state_out")
        metrics = (state or {}).get("metrics")
        if not isinstance(metrics, dict):
            self.fail("STAPostPNR state_out.json has no metrics object")
            metrics = {}
        for corner in CORNERS:
            row = {}
            self.report["corners"][corner] = row
            for name in list(CORNER_COUNTS) + list(CORNER_SLACKS):
                key = "%s__corner:%s" % (name, corner)
                v = number(metrics.get(key))
                row[name] = v
                if v is None:
                    self.fail("%s: metric %s missing or not a finite number (%r)" % (corner, key, metrics.get(key)))
                elif v < 0:
                    self.fail("%s: %s = %s is negative" % (corner, name, v))
                elif name in CORNER_COUNTS and (v != int(v) or v != 0):
                    self.fail("%s: %s = %s (must be 0)" % (corner, name, v))
            rpt = os.path.join(sta_dir, corner, "checks.rpt")
            if not os.path.isfile(rpt):
                self.fail("%s: report missing: %s" % (corner, rpt))
                continue
            self.sources[os.path.relpath(rpt, self.run_dir)] = sha256(rpt)
            with open(rpt, encoding="utf-8", errors="replace") as f:
                text = f.read()
            for name, footer in CORNER_COUNTS.items():
                if footer is None:
                    continue
                m = re.findall(r"^%s (\d+)\s*$" % footer, text, re.M)
                if len(m) != 1:
                    self.fail("%s: checks.rpt has %d '%s' lines (expected 1)" % (corner, len(m), footer))
                elif row[name] is not None and int(m[0]) != row[name]:
                    self.fail("%s: checks.rpt '%s %s' != metric %s" % (corner, footer, m[0], row[name]))

    def final(self, sta_dir):
        path = os.path.join(self.run_dir, "final", "metrics.json")
        if not os.path.isfile(path):
            self.fail("final/metrics.json missing (the flow did not finish its final views; a deferred checker "
                      "error or a failed step is the likely reason) - no DRC/LVS/antenna result, FAIL")
            return
        m = self.read(path, "final metrics")
        if m is None:
            return
        fin = {}
        self.report["final"] = fin
        for key in FINAL_ZERO + FINAL_ZERO_IF_PRESENT + ("design__instance__utilization",):
            if key not in m and key in FINAL_ZERO_IF_PRESENT:
                continue
            v = number(m.get(key))
            fin[key] = v
            if v is None:
                self.fail("final metric %s missing or not a finite number (%r)" % (key, m.get(key)))
            elif key == "design__instance__utilization":
                if not 0 <= v <= 1:
                    self.fail("utilization %s outside 0..1" % v)
            elif v != 0:
                self.fail("final %s = %s (must be 0)" % (key, v))
        for corner in CORNERS:
            for name in CORNER_COUNTS:
                key = "%s__corner:%s" % (name, corner)
                row = self.report["corners"].get(corner, {})
                if key in m and row.get(name) is not None and number(m[key]) != row[name]:
                    self.fail("%s: final %s = %r != STAPostPNR %r" % (corner, name, m[key], row[name]))

    def netlist(self):
        files = sorted(glob.glob(os.path.join(self.run_dir, "final", "nl", "*.nl.v")) +
                       glob.glob(os.path.join(self.run_dir, "final", "pnl", "*.pnl.v")))
        if not files:
            self.fail("no final netlist (final/nl/*.nl.v or final/pnl/*.pnl.v)")
            return
        # Real HD cell-name tokens of each netlist, matched against the exclusion PATTERNS with
        # fnmatchcase (the patterns are never turned into regular expressions).
        hits = {}
        for path in files:
            self.sources[os.path.relpath(path, self.run_dir)] = sha256(path)
            with open(path, encoding="utf-8", errors="replace") as f:
                tokens = re.findall(r"\bsky130_fd_sc_hd__\w+", f.read())
            if not tokens:
                self.fail("%s contains no sky130_fd_sc_hd cell (an empty netlist proves nothing)"
                          % os.path.basename(path))
            for token in tokens:
                for pattern in EXCLUDED:
                    if fnmatch.fnmatchcase(token, pattern):
                        key = "%s (%s) in %s" % (token, pattern, os.path.basename(path))
                        hits[key] = hits.get(key, 0) + 1
        self.report["excluded_cell_hits"] = hits
        if hits:
            self.fail("excluded cells still in the final netlist: %s" % hits)

    def run(self):
        if not os.path.isdir(self.run_dir):
            self.fail("run directory missing: %s" % self.run_dir)
        else:
            self.policy()
            sta = self.one_step("OpenROAD.STAPostPNR")
            if sta:
                self.config(sta)
                self.corners(sta)
            self.cts()
            self.checkers()
            self.final(sta)
            self.netlist()
        # Aggregates only when all nine corners have a valid value; otherwise None (never a made-up 0).
        # known_max_partial is the max over the corners that DO have a value, labelled as partial.
        rows = self.report["corners"]
        values = {name: [rows.get(c, {}).get(name) for c in CORNERS] for name in CORNER_COUNTS}
        complete = {name: all(v is not None for v in vals) for name, vals in values.items()}
        self.report["totals"] = {n: sum(values[n]) if complete[n] else None for n in CORNER_COUNTS}
        self.report["max_over_corners"] = {n: max(values[n]) if complete[n] else None for n in CORNER_COUNTS}
        self.report["known_max_partial"] = {n: max([v for v in values[n] if v is not None], default=None)
                                            for n in CORNER_COUNTS if not complete[n]}
        self.report["sources_sha256"] = self.sources
        self.report["problems"] = self.problems
        self.report["verdict"] = "PASS" if not self.problems else "FAIL"
        return self.report


def markdown(rep):
    lines = ["# PHYSICAL_SIGNOFF (%s)" % rep["verdict"], "",
             "Run `%s`, git `%s`. Policy: sidecar section 9. A FAIL lists every problem; nothing is waived." %
             (rep["run_dir"], rep.get("git_sha")), "",
             "| corner | setup ws | hold ws | setup vio | hold vio | max slew | max fanout | max cap |",
             "|---|---|---|---|---|---|---|---|"]
    for c, r in rep["corners"].items():
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            c, r.get("timing__setup__ws"), r.get("timing__hold__ws"), r.get("timing__setup_vio__count"),
            r.get("timing__hold_vio__count"), r.get("design__max_slew_violation__count"),
            r.get("design__max_fanout_violation__count"), r.get("design__max_cap_violation__count")))
    lines += ["", "Worst corner counts (None = not all 9 corners valid): %s" % rep.get("max_over_corners"),
              "Partial maxima over valid corners only: %s" % rep.get("known_max_partial"),
              "Final: %s" % rep.get("final"), "Checker corners: %s" % rep.get("checker_corners"),
              "Excluded cells in final netlist: %s" % rep.get("excluded_cell_hits"),
              "CTS buffers: %s" % rep.get("cts"), ""]
    if rep["problems"]:
        lines += ["Problems:"] + ["- " + p for p in rep["problems"]] + [""]
    lines += ["Sources (sha256):"] + ["- `%s` %s" % kv for kv in sorted(rep["sources_sha256"].items())]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="runs/wokwi")
    ap.add_argument("--out", default="physical-signoff")
    ap.add_argument("--root", default=ROOT, help="repository root (for the sidecar); default: this checkout")
    args = ap.parse_args(argv)
    rep = Audit(args.run_dir, args.root).run()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "physical-signoff.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, sort_keys=True)
    with open(os.path.join(args.out, "PHYSICAL_SIGNOFF.md"), "w", encoding="utf-8") as f:
        f.write(markdown(rep))
    for p in rep["problems"]:
        print("PHYSICAL_SIGNOFF FAIL:", p)
    print("PHYSICAL_SIGNOFF %s: worst-corner counts %s; %d problem(s); report in %s"
          % (rep["verdict"], rep["max_over_corners"], len(rep["problems"]), args.out))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
