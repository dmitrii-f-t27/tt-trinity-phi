# SPDX-License-Identifier: Apache-2.0
"""Pinned inputs and fail-closed helpers of the READ-ONLY stage-41 observation (sidecar section 9b).

Standard library only. Imported by fetch_verify.py and summarize.py on the runner and by the tests.
"""
import hashlib
import os
import re
import zipfile

REPO = "dmitrii-f-t27/tt-trinity-phi"
RUN_ID = 38855207187
ARTIFACT_ID = 12026633781
ARTIFACT_NAME = "GDS_logs"
ARTIFACT_SIZE = 35525020
MEASURED_SHA = "f6f4db6bd0c7dcdefb46b6a87967db8b322ea272"
ZIP_SHA256 = "8a48797fa77e74e60b4790f4dbdf7eaf0fa6ca8946fd963c46c3b1439c6b2339"

IMAGE = "ghcr.io/librelane/librelane@sha256:f91b21d75f79871f9ccf37451020b5d2f7b3236881a997ff709c561e8280a30f"
IMAGE_ID = "sha256:897f703b9de811e792eb22023fd0986418bbbd9ae5ff511eb74419008eac810f"
LIBRELANE_VERSION = "3.0.14"
PDK_FAMILY = "sky130"
PDK_VERSION = "8afc8346a57fe1ab7934ba5a6056ea8b43078e71"
# Absolute locations used by the archived run; the observation mounts the same paths so the archived
# stage-41 config is used unchanged (no path rebasing).
PDK_ROOT = "/home/runner/.ciel"
PDK_VERSION_ROOT = PDK_ROOT + "/ciel/sky130/versions/" + PDK_VERSION  # PDK_ROOT value in the archived config
WORK_ROOT = "/home/runner/work/tt-trinity-phi/tt-trinity-phi"
RUNS_MOUNT = WORK_ROOT + "/runs"
# sha256 of LibreLane 3.0.14 scripts/base.sdc (the archived FALLBACK_SDC), from the cached primary source
BASE_SDC_SHA256 = "34c8f331c366db51a78ed8a0d2a7bbac3b6c1cfe0277a04c6aca9ba4948e7f78"

STAGE41 = "runs/wokwi/41-openroad-repairdesignpostgrt/"
STAGE41_MEMBERS = {
    STAGE41 + "tt_um_trinity_nano.odb": "f0179addc9d48bc63a56d6d63d85e4d0c2d69ac244709ae0bd106981d75f7158",
    STAGE41 + "config.json": "ad18a01ca05bbf2c87ee3d2f14ef6029e2f31d81423d9cf0de4ab4e9a769ddad",
    STAGE41 + "state_out.json": "767d2c261f0033c693776c52d39bbdb32a585a0031317b70b9ac31e0af90fd52",
    STAGE41 + "tt_um_trinity_nano.sdc": "7d7d6e0593a574e81fcc53e34f352d9f44a60033ce0f2352276c7a879e057394",
    STAGE41 + "tt_um_trinity_nano.def": "96c165a8edd53d865799ef87435366a08ac2420f46b4053e8c2a9f6f6783e6dd",
    STAGE41 + "openroad-repairdesignpostgrt.log": "65bc28892ac5422af8918ce3d89b75884ebe001786f6b3f920ef8df97af09af6",
}

CORNERS = ["nom_tt_025C_1v80", "nom_ss_100C_1v60", "nom_ff_n40C_1v95",
           "min_tt_025C_1v80", "min_ss_100C_1v60", "min_ff_n40C_1v95",
           "max_tt_025C_1v80", "max_ss_100C_1v60", "max_ff_n40C_1v95"]
# Final strict sign-off of f6f4db6 (max-slew violating pins per corner), the reference to compare with
FINAL_SLEW_PINS = {"max_ss_100C_1v60": 293, "nom_ss_100C_1v60": 228, "min_ss_100C_1v60": 147,
                   "max_tt_025C_1v80": 11}
KINDS = ("slew", "cap", "fanout")
FINAL_STA_DIR = "runs/wokwi/56-openroad-stapostpnr/"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_artifact_metadata(meta):
    """Problems (empty list = OK) of the GitHub artifact metadata against the pinned artifact."""
    problems = []
    if not isinstance(meta, dict):
        return ["artifact metadata is not an object"]
    run = meta.get("workflow_run") or {}
    want = {"id": ARTIFACT_ID, "name": ARTIFACT_NAME, "size_in_bytes": ARTIFACT_SIZE, "expired": False}
    for key, value in want.items():
        if meta.get(key) != value:
            problems.append("artifact %s is %r, expected %r" % (key, meta.get(key), value))
    if run.get("id") != RUN_ID:
        problems.append("artifact workflow_run.id is %r, expected %r" % (run.get("id"), RUN_ID))
    if run.get("head_sha") != MEASURED_SHA:
        problems.append("artifact workflow_run.head_sha is %r, expected %s" % (run.get("head_sha"), MEASURED_SHA))
    return problems


def safe_extract(zip_path, dest):
    """Extract every member below dest; refuse absolute paths, '..', symlinks and duplicates."""
    dest_real = os.path.realpath(dest)
    seen = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name in seen:
                raise ValueError("duplicate zip member: %r" % name)
            seen.add(name)
            if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or ".." in name.replace("\\", "/").split("/"):
                raise ValueError("unsafe zip member path: %r" % name)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("symlink zip member refused: %r" % name)
            target = os.path.realpath(os.path.join(dest_real, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise ValueError("zip member escapes destination: %r" % name)
        zf.extractall(dest_real)
    return sorted(seen)


ROW = re.compile(r"^(\S+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+\(VIOLATED\)\s*$")


def parse_rows(text):
    """Rows of ONE report_check_types block -> [(pin, limit, value, slack)]. Every line that ends in
    '(VIOLATED)' must be a well-formed row, otherwise ValueError. An empty block gives []."""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if s.endswith("(VIOLATED)"):
            m = ROW.match(s)
            if not m:
                raise ValueError("malformed violator row: %r" % s)
            rows.append((m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))))
    return rows


FOOTER = {"slew": "max slew violation count", "cap": "max cap violation count",
          "fanout": "max fanout violation count"}


def parse_final_counts(text):
    """Archived final checks.rpt -> {kind: count} from its footer lines (exactly one per kind), and the
    number of max-slew rows, which must equal the slew footer. ValueError otherwise."""
    counts = {}
    for kind, label in FOOTER.items():
        found = re.findall(r"^%s (\d+)\s*$" % label, text, re.M)
        if len(found) != 1:
            raise ValueError("final report has %d '%s' lines" % (len(found), label))
        counts[kind] = int(found[0])
    region = re.search(r"^max slew\s*$(.*?)(?=^max (?:capacitance|fanout)\s*$|^=+\s*$)", text, re.M | re.S)
    slew_rows = parse_rows(region.group(1)) if region else []
    if len(slew_rows) != counts["slew"]:
        raise ValueError("final report has %d max-slew rows but footer %d" % (len(slew_rows), counts["slew"]))
    return counts
