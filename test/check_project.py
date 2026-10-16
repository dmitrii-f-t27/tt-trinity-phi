#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed consistency gate for the SKY26d DOT4 project.

One truth for the source list: info.yaml `source_files` == test/Makefile PROJECT_SOURCES ==
the files that the top module `tt_um_trinity_nano` actually instantiates (transitively).
It also checks: top module name, 1x1 tile, full 8+8+8 pinout, the official @ttsky26d action pin in
the TT workflows (and no older shuttle tag), test/requirements.txt, and that the active
RTL contains no arithmetic multiply operator (R-SI-1; sensitivity lists and attributes are
not arithmetic). Python 3 standard library only.
Usage (repo root): python3 test/check_project.py        (exit 0 = consistent)
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
TOP = "tt_um_trinity_nano"
# commit that the annotated tag TinyTapeout/tt-gds-action@ttsky26d dereferences to
TT_PIN = "395eedb3eeec3c2f4961c3b04ff2cf87f5525636"

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


def main():
    top, info_files, tiles, clock, pins = info_yaml()
    make_files = makefile_sources()
    mods = module_map()
    if top != TOP:
        fail("info.yaml top_module is %r, expected %s" % (top, TOP))
    if tiles != "1x1":
        fail("info.yaml tiles is %r, expected 1x1" % tiles)
    if clock != 50000000:
        fail("info.yaml clock_hz is %r, expected 50000000" % clock)
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
    workflows()
    req = os.path.join(HERE, "requirements.txt")
    if not os.path.isfile(req) or "cocotb==2.0.1" not in read(req):
        fail("test/requirements.txt must pin cocotb==2.0.1")
    if errors:
        print("CHECK_PROJECT FAIL: %d problem(s)" % len(errors))
        return 1
    print("CHECK_PROJECT PASS: top %s, 1x1, %d source files == Makefile == instantiated: %s"
          % (TOP, len(info_files), ", ".join(sorted(info_files))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
