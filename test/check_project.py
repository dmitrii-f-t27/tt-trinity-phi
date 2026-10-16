#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed consistency gate for the SKY26d DOT4 project.

One truth for the source list: info.yaml `source_files` == test/Makefile PROJECT_SOURCES ==
the files that the top module `tt_um_trinity_nano` actually instantiates (transitively).
It also checks: top module name, the tile shape and clock of the selected physical profile, full
8+8+8 pinout, the official @ttsky26d action pin in the TT workflows (and no older shuttle tag),
test/requirements.txt, and that the active RTL contains no arithmetic multiply operator (R-SI-1;
sensitivity lists and attributes are not arithmetic). Python 3 standard library only.

Physical profile, chosen by the environment variable PHI_PROFILE (exact values only):
  unset / "default"  1x1, clock_hz 50000000, CLOCK_PERIOD 20 (the original #100 envelope)
  "eval-1x2"         isolated evaluation: 1x2, clock_hz 25000000, CLOCK_PERIOD 40, density 65,
                     AREA 1, MFS3 False, AREA_USE_NF False, the three excluded HD delay-cell
                     families plus exactly buf_1, clkbuf_1 and clkbuf_2 (section 9e) and exactly
                     nor2_1 and nor2_2 (section 9f), strict
                     timing/slew/cap checker corners ["*"], CTS 20 / 50 um with the buffer list
                     [clkbuf_8, clkbuf_4] and root clkbuf_16, repair
                     margins 20 / 20 % and post-global-route repair 10 / 10 % with re-route
                     (sidecar section 9a); the constants of
                     specs/physical/gf16_dot4_sky26d_1x2_25mhz_eval.t27 must agree with info.yaml and
                     src/config.json, its latency must equal LATENCY_CLOCKS = 8 of the numeric spec, and
                     the numeric spec must be byte-identical to NUMERIC_SPEC_SHA256.
Any other value fails. A profile only selects exact expected values; no check is relaxed.
Usage (repo root): python3 test/check_project.py                       (exit 0 = consistent)
                   PHI_PROFILE=eval-1x2 python3 test/check_project.py
"""
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
TOP = "tt_um_trinity_nano"
# commit that the annotated tag TinyTapeout/tt-gds-action@ttsky26d dereferences to
TT_PIN = "395eedb3eeec3c2f4961c3b04ff2cf87f5525636"

NUMERIC_SPEC = os.path.join(ROOT, "specs", "numeric", "gf16_dot4_sky26d.t27")
NUMERIC_SPEC_SHA256 = "01249989ab5688eb78fc6950880238102daf6608b7cf1946b29b7277f53003c9"
PHYSICAL_SPEC = os.path.join(ROOT, "specs", "physical", "gf16_dot4_sky26d_1x2_25mhz_eval.t27")

# Exact expectations per physical profile; `config` lists the src/config.json keys that must hold
# exactly these values (keys not listed are not touched by a profile).
PROFILES = {
    "default": {
        "tiles": "1x1", "clock_hz": 50000000, "sidecar": None,
        "config": {"CLOCK_PERIOD": 20},
    },
    "eval-1x2": {
        "tiles": "1x2", "clock_hz": 25000000, "sidecar": PHYSICAL_SPEC,
        "config": {"CLOCK_PERIOD": 40, "PL_TARGET_DENSITY_PCT": 65, "SYNTH_STRATEGY": "AREA 1",
                   "SYNTH_ABC_USE_MFS3": False, "SYNTH_ABC_AREA_USE_NF": False,
                   # physical closure (sidecar section 9): the three HD delay-cell families excluded
                   # (stricter mapping), all checkers strict, experimental CTS geometry 20 / 50 um;
                   # limits 0.75 ns / fanout 10 are not touched here
                   # sidecar section 9e: plus the exact buffer drive floor (buf_1, clkbuf_1, clkbuf_2 only;
                   # buf_2 and up, clkbuf_4 and up stay available); section 9f: plus the exact NOR2 drive
                   # floor (nor2_1, nor2_2 only; nor2_4 / nor2_8 and the NOR2B cells are not listed here)
                   "EXTRA_EXCLUDED_CELLS": ["sky130_fd_sc_hd__clkdlybuf*", "sky130_fd_sc_hd__dlygate*",
                                            "sky130_fd_sc_hd__dlymetal*", "sky130_fd_sc_hd__buf_1",
                                            "sky130_fd_sc_hd__clkbuf_1", "sky130_fd_sc_hd__clkbuf_2",
                                            "sky130_fd_sc_hd__nor2_1", "sky130_fd_sc_hd__nor2_2"],
                   "TIMING_VIOLATION_CORNERS": ["*"], "MAX_SLEW_VIOLATION_CORNERS": ["*"],
                   "MAX_CAP_VIOLATION_CORNERS": ["*"],
                   "CTS_DISTANCE_BETWEEN_BUFFERS": 20, "CTS_CLK_MAX_WIRE_LENGTH": 50,
                   # section 9e: the CTS buffer list is used as given by cts.tcl (no dont-use filter), so
                   # it is set explicitly without clkbuf_2; the root buffer stays clkbuf_16
                   "CTS_CLK_BUFFERS": ["sky130_fd_sc_hd__clkbuf_8", "sky130_fd_sc_hd__clkbuf_4"],
                   "CTS_ROOT_BUFFER": "sky130_fd_sc_hd__clkbuf_16",
                   # sidecar section 9a: post-placement repair margins at the official 20 / 20 %, and the
                   # official (experimental) post-global-route repair with its defaults 10 / 10 % and the
                   # re-route on; the constraints (0.75 ns, fanout 10, library max cap) are not raised
                   "DESIGN_REPAIR_MAX_SLEW_PCT": 20, "DESIGN_REPAIR_MAX_CAP_PCT": 20,
                   "RUN_POST_GRT_DESIGN_REPAIR": True, "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 10,
                   "GRT_DESIGN_REPAIR_MAX_CAP_PCT": 10, "GRT_DESIGN_REPAIR_RUN_GRT": True},
    },
}

errors = []


def fail(msg):
    errors.append(msg)
    print("FAIL:", msg)


def read(path):
    with open(path) as f:
        return f.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def info_yaml():
    text = read(os.path.join(ROOT, "info.yaml"))
    m = re.search(r"^\s*top_module:\s*\"?(\w+)\"?", text, re.M)
    top = m.group(1) if m else None
    files = []
    block = re.search(r"^\s*source_files:\s*\n((?:\s*(?:-\s*.*|#.*)?\n)+)", text, re.M)
    if block:
        for ln in block.group(1).splitlines():
            mm = re.match(r"^\s*-\s*\"?([\w./-]+)\"?\s*(?:#.*)?$", ln)
            if mm:
                files.append(mm.group(1))
    tiles = re.search(r"^\s*tiles:\s*\"?([\dx]+)\"?", text, re.M)
    clock = re.search(r"^\s*clock_hz:\s*(\d+)", text, re.M)
    pins = re.findall(r"^\s+(ui|uo|uio)\[(\d)\]:", text, re.M)
    return top, files, (tiles.group(1) if tiles else None), (int(clock.group(1)) if clock else None), pins


def makefile_sources():
    text = read(os.path.join(HERE, "Makefile"))
    m = re.search(r"^PROJECT_SOURCES\s*=\s*((?:[^\n]*\\\n)*[^\n]*)", text, re.M)
    if not m:
        return None
    return [t for t in m.group(1).replace("\\", " ").split() if t.endswith(".v")]


def module_map():
    mods = {}
    for path in glob.glob(os.path.join(SRC, "*.v")):
        for name in re.findall(r"^\s*module\s+(\w+)", strip_comments(read(path)), re.M):
            mods.setdefault(name, os.path.basename(path))
    return mods


def instantiated_files(mods):
    seen_mods = set()
    todo = [TOP]
    files = set()
    while todo:
        name = todo.pop()
        if name in seen_mods:
            continue
        seen_mods.add(name)
        if name not in mods:
            fail("module %s is instantiated but not defined in src/" % name)
            continue
        fname = mods[name]
        files.add(fname)
        text = strip_comments(read(os.path.join(SRC, fname)))
        # body of this module only
        mm = re.search(r"\bmodule\s+%s\b(.*?)\bendmodule\b" % name, text, re.S)
        body = mm.group(1) if mm else text
        for inst, _ in re.findall(r"^\s*(\w+)\s*(?:#\s*\([^;]*?\)\s*)?(\w+)\s*\(", body, re.M):
            if inst in mods:
                todo.append(inst)
    return files


def star_gate(files):
    for fname in files:
        text = strip_comments(read(os.path.join(SRC, fname)))
        text = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)       # attributes
        text = re.sub(r"@\s*\(?\s*\*\s*\)?", " ", text)             # sensitivity lists
        if "*" in text:
            line = [i + 1 for i, l in enumerate(text.splitlines()) if "*" in l]
            fail("%s contains an arithmetic '*' near line(s) %s (R-SI-1)" % (fname, line[:5]))


TECH_CELL = "sky130_fd_sc_hd__fa_1"
TECH_WRAPPER = "gf16_fa1"
TECH_FILE = "gf16_sig_mul10.v"


def tech_cell_gate():
    """Technology cells in src/ are forbidden except ONE: sky130_fd_sc_hd__fa_1, instantiated exactly once,
    inside the wrapper module gf16_fa1 of gf16_sig_mul10.v, and only in the branch that is compiled when
    PHI_FA_PHYS (set by the official __pnr__ flow define) is on. No other sky130 name may appear in any
    src file (the unknown-instance check above ignores names that are not project modules, so this gate
    is what keeps the allow-list narrow). The PDK or black-box definition supplies the cell; src/ never
    declares it."""
    for path in sorted(glob.glob(os.path.join(SRC, "*.v"))):
        fname = os.path.basename(path)
        text = strip_comments(read(path))
        for name in sorted(set(re.findall(r"\bsky130\w*", text))):
            if name != TECH_CELL:
                fail("%s mentions technology cell/name %s (only %s is allowed)" % (fname, name, TECH_CELL))
        if TECH_CELL not in text:
            continue
        if fname != TECH_FILE:
            fail("%s instantiates %s (allowed only in %s)" % (fname, TECH_CELL, TECH_FILE))
            continue
        if len(re.findall(r"\b%s\b" % TECH_CELL, text)) != 1:
            fail("%s must mention %s exactly once" % (fname, TECH_CELL))
        m = re.search(r"\bmodule\s+%s\b(.*?)\bendmodule\b" % TECH_WRAPPER, text, re.S)
        if not m or TECH_CELL not in m.group(1):
            fail("%s must be instantiated inside module %s" % (TECH_CELL, TECH_WRAPPER))
        elif not re.search(r"`ifdef\s+PHI_FA_PHYS\b(.*?)`else", m.group(1), re.S) \
                or TECH_CELL not in re.search(r"`ifdef\s+PHI_FA_PHYS\b(.*?)`else", m.group(1), re.S).group(1):
            fail("%s must sit in the `ifdef PHI_FA_PHYS branch of %s" % (TECH_CELL, TECH_WRAPPER))
        if not re.search(r"`ifdef\s+__pnr__\s*`define\s+PHI_FA_PHYS\s*`endif", text):
            fail("%s must enable PHI_FA_PHYS only through `ifdef __pnr__" % fname)
        if re.search(r"\bmodule\s+sky130", text):
            fail("%s declares a sky130 module (the PDK supplies it)" % fname)


def workflows():
    for name in ("gds.yaml", "docs.yaml"):
        path = os.path.join(ROOT, ".github", "workflows", name)
        if not os.path.isfile(path):
            fail("missing workflow " + name)
            continue
        text = read(path)
        uses = re.findall(r"uses:\s*(TinyTapeout/\S+)", text)
        if not uses:
            fail("%s has no TinyTapeout action" % name)
        for u in uses:
            if "@" + TT_PIN not in u:
                fail("%s: %s is not pinned to %s (official @ttsky26d)" % (name, u, TT_PIN))
        if re.search(r"ttsky26[abc]", text):
            fail("%s still references an older shuttle tag" % name)
        if "continue-on-error" in text:
            fail("%s uses continue-on-error" % name)


def yaml_guard():
    """Workflow files that this project owns must parse. Stdlib check for the classic mistake (an
    unquoted `name:` value containing `: ` or ` #`), plus a full parse when PyYAML is installed."""
    for name in ("test.yaml", "docs.yaml", "gds.yaml", "no_star.yaml"):
        path = os.path.join(ROOT, ".github", "workflows", name)
        if not os.path.isfile(path):
            continue
        text = read(path)
        for n, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^\s*(?:-\s*)?name:\s*(.+?)\s*$", line)
            if not m:
                continue
            val = m.group(1)
            if val[0] not in "\"'" and (": " in val or " #" in val):
                fail("%s:%d unquoted name value contains ': ' or ' #' (YAML scalar error): %s" % (name, n, val))
        try:
            import yaml  # optional
        except ImportError:
            continue
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as e:
            fail("%s does not parse as YAML: %s" % (name, e))


def spec_consts(path):
    """`pub const NAME : type = value;` lines of a .t27 file, integer values only."""
    consts = {}
    for name, val in re.findall(r"^pub const (\w+)\s*:\s*\w+\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;", read(path), re.M):
        consts[name] = int(val, 0)
    return consts


def profile_gate(name, prof, tiles, clock):
    """Exact tile shape, clock and config values of the selected profile, and for the evaluation
    profile the agreement of the physical sidecar with info.yaml, config.json and the numeric spec."""
    if tiles != prof["tiles"]:
        fail("info.yaml tiles is %r, expected %s (profile %s)" % (tiles, prof["tiles"], name))
    if clock != prof["clock_hz"]:
        fail("info.yaml clock_hz is %r, expected %d (profile %s)" % (clock, prof["clock_hz"], name))
    cfg = json.loads(read(os.path.join(SRC, "config.json")))
    for key, want in prof["config"].items():
        got = cfg.get(key, "<missing>")
        # exact type too: False must not match 0, 40 must not match 40.0
        if type(got) is not type(want) or got != want:
            fail("src/config.json %s is %r, expected %r (profile %s)" % (key, got, want, name))
    for key in prof.get("absent", []):
        if key in cfg:
            fail("src/config.json sets %s = %r; profile %s requires the official default" % (key, cfg[key], name))
    period = cfg.get("CLOCK_PERIOD")
    if not isinstance(clock, int) or not isinstance(period, int) or clock * period != 10 ** 9:
        fail("info.yaml clock_hz %r and src/config.json CLOCK_PERIOD %r ns are not one clock" % (clock, period))
    if prof["sidecar"] is None:
        return
    with open(NUMERIC_SPEC, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != NUMERIC_SPEC_SHA256:
        fail("numeric spec sha256 %s != %s (the functional SSOT must stay byte for byte)" % (digest, NUMERIC_SPEC_SHA256))
    if not os.path.isfile(prof["sidecar"]):
        fail("physical sidecar missing: %s" % os.path.relpath(prof["sidecar"], ROOT))
        return
    side = spec_consts(prof["sidecar"])
    num = spec_consts(NUMERIC_SPEC)
    want = {"TILES_X": int(prof["tiles"].split("x")[0]), "TILES_Y": int(prof["tiles"].split("x")[1]),
            "REQUIRED_TILE_UNITS": 2, "CLOCK_HZ": prof["clock_hz"], "CLOCK_PERIOD_NS": prof["config"]["CLOCK_PERIOD"],
            "LATENCY_ENABLED_CLOCKS": 8, "SIGNOFF_STA_CORNERS": 9, "SIGNOFF_MAX_SLEW_PS": 750,
            "SIGNOFF_MAX_FANOUT": 10, "SIGNOFF_ALLOWED_VIOLATIONS": 0}
    for key, val in want.items():
        if side.get(key) != val:
            fail("sidecar %s is %r, expected %r" % (key, side.get(key), val))
    if num.get("LATENCY_CLOCKS") != 8 or side.get("LATENCY_ENABLED_CLOCKS") != num.get("LATENCY_CLOCKS"):
        fail("latency: sidecar LATENCY_ENABLED_CLOCKS %r, numeric LATENCY_CLOCKS %r (both must be 8)"
             % (side.get("LATENCY_ENABLED_CLOCKS"), num.get("LATENCY_CLOCKS")))


def main():
    yaml_guard()
    name = os.environ.get("PHI_PROFILE", "default")     # set but empty is unknown, not default
    prof = PROFILES.get(name)
    if prof is None:
        fail("unknown PHI_PROFILE %r (known: %s)" % (name, ", ".join(sorted(PROFILES))))
        print("CHECK_PROJECT FAIL: %d problem(s)" % len(errors))
        return 1
    top, info_files, tiles, clock, pins = info_yaml()
    make_files = makefile_sources()
    mods = module_map()
    if top != TOP:
        fail("info.yaml top_module is %r, expected %s" % (top, TOP))
    profile_gate(name, prof, tiles, clock)
    for kind in ("ui", "uo", "uio"):
        if sorted(i for k, i in pins if k == kind) != [str(n) for n in range(8)]:
            fail("info.yaml pinout must list %s[0..7] exactly once" % kind)
    if make_files is None:
        fail("test/Makefile has no PROJECT_SOURCES")
        make_files = []
    inst = instantiated_files(mods)
    for f in info_files + make_files:
        if not os.path.isfile(os.path.join(SRC, f)):
            fail("listed source file does not exist: src/" + f)
    if len(set(info_files)) != len(info_files):
        fail("duplicate entries in info.yaml source_files")
    if set(info_files) != set(make_files):
        fail("info.yaml source_files %s != Makefile PROJECT_SOURCES %s"
             % (sorted(info_files), sorted(make_files)))
    if set(info_files) != inst:
        fail("info.yaml source_files %s != files instantiated from %s: %s"
             % (sorted(info_files), TOP, sorted(inst)))
    star_gate(sorted(inst))
    tech_cell_gate()
    workflows()
    req = os.path.join(HERE, "requirements.txt")
    if not os.path.isfile(req) or "cocotb==2.0.1" not in read(req):
        fail("test/requirements.txt must pin cocotb==2.0.1")
    if errors:
        print("CHECK_PROJECT FAIL: %d problem(s)" % len(errors))
        return 1
    print("CHECK_PROJECT PASS: profile %s, top %s, %s, %d Hz, %d source files == Makefile == instantiated: %s"
          % (name, TOP, tiles, clock, len(info_files), ", ".join(sorted(info_files))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
