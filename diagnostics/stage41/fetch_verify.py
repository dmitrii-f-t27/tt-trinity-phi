#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Download the pinned archived GDS_logs artifact of f6f4db6 and verify it before any use (sidecar 9b).

Checks, in this order, each fail closed: artifact metadata (id, name, size, not expired, run id, head SHA);
downloaded size and zip sha256; safe extraction (no absolute/'..'/symlink members); sha256 of the pinned stage-41
members; then the sha256 of every archive file the observation uses (stage-41 directory, every view referenced
by the stage-41 state_out.json, the 9 final checks.rpt) is recorded, and a missing one fails.
Errors are reported by phase, exception class and HTTP status only: no URL, header or token text.
Standard library only. Usage: GH_TOKEN=... python3 fetch_verify.py --dest DIR --report FILE.json
"""
import argparse
import glob
import json
import os
import socket
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

API = "https://api.github.com/repos/%s/actions/artifacts/%d" % (c.REPO, c.ARTIFACT_ID)
TIMEOUT = 60  # seconds per connection / read; the download is also capped at the pinned size


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _headers(token):
    return {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "phi-stage41-diagnostic"}


def _err(phase, e):
    code = getattr(e, "code", None)
    return "%s failed: %s%s" % (phase, type(e).__name__, " (HTTP %s)" % code if code else "")


def used_files(extract):
    """Archive files the observation reads: the stage-41 directory, every view path in its state_out.json
    that lies under the archived runs directory, and the 9 final checks.rpt."""
    files = {p for p in glob.glob(os.path.join(extract, c.STAGE41, "*")) if os.path.isfile(p)}
    with open(os.path.join(extract, c.STAGE41, "state_out.json"), encoding="utf-8") as f:
        state = json.load(f)

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith(c.WORK_ROOT + "/runs/"):
            files.add(os.path.join(extract, v[len(c.WORK_ROOT) + 1:]))

    walk({k: v for k, v in state.items() if k != "metrics"})
    for corner in c.CORNERS:
        files.add(os.path.join(extract, c.FINAL_STA_DIR, corner, "checks.rpt"))
    return sorted(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN")
    report = {"repo": c.REPO, "run_id": c.RUN_ID, "artifact_id": c.ARTIFACT_ID, "problems": []}
    P = report["problems"]

    def finish():
        report["status"] = "PASS" if not P else "FAIL"
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        for p in P:
            print("STAGE41_FETCH FAIL:", p)
        print("STAGE41_FETCH", report["status"])
        return 0 if not P else 1

    if not token:
        P.append("GH_TOKEN is not set")
        return finish()
    try:
        with urllib.request.urlopen(urllib.request.Request(API, headers=_headers(token)), timeout=TIMEOUT) as r:
            meta = json.load(r)
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as e:
        P.append(_err("metadata", e))
        return finish()
    report["metadata"] = {k: meta.get(k) for k in ("id", "name", "size_in_bytes", "expired", "created_at",
                                                     "expires_at")}
    report["metadata"]["workflow_run"] = {k: (meta.get("workflow_run") or {}).get(k) for k in ("id", "head_sha")}
    P += c.check_artifact_metadata(meta)
    if P:
        return finish()

    location = None
    try:
        urllib.request.build_opener(_NoRedirect).open(
            urllib.request.Request(API + "/zip", headers=_headers(token)), timeout=TIMEOUT)
        P.append("redirect failed: no redirect returned")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            location = e.headers.get("Location")
        else:
            P.append(_err("redirect", e))
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        P.append(_err("redirect", e))
    if not location:
        if not P:
            P.append("redirect failed: no location")
        return finish()
    os.makedirs(args.dest, exist_ok=True)
    zip_path = os.path.join(args.dest, "GDS_logs.zip")
    try:
        # the signed URL needs no token; the token is not sent there
        with urllib.request.urlopen(urllib.request.Request(location), timeout=TIMEOUT) as r, \
                open(zip_path, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > c.ARTIFACT_SIZE:
                    raise OSError("download larger than the pinned size")
                f.write(chunk)
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        P.append(_err("download", e))
        return finish()
    del location
    report["zip_size"] = os.path.getsize(zip_path)
    report["zip_sha256"] = c.sha256_file(zip_path)
    if report["zip_size"] != c.ARTIFACT_SIZE:
        P.append("zip size %d != %d" % (report["zip_size"], c.ARTIFACT_SIZE))
    if report["zip_sha256"] != c.ZIP_SHA256:
        P.append("zip sha256 %s != %s" % (report["zip_sha256"], c.ZIP_SHA256))
    if P:
        return finish()
    extract = os.path.join(args.dest, "extract")
    try:
        report["member_count"] = len(c.safe_extract(zip_path, extract))
    except (ValueError, OSError) as e:
        P.append("extraction refused: %s" % e)
        return finish()
    for name, want in c.STAGE41_MEMBERS.items():
        path = os.path.join(extract, name)
        got = c.sha256_file(path) if os.path.isfile(path) else None
        if got != want:
            P.append("member %s sha256 %s != %s" % (name, got, want))
    report["used_files"] = {}
    for path in used_files(extract):
        rel = os.path.relpath(path, extract)
        report["used_files"][rel] = c.sha256_file(path) if os.path.isfile(path) else None
        if report["used_files"][rel] is None:
            P.append("used archive file missing: %s" % rel)
    report["runs_dir"] = os.path.join(extract, "runs")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
