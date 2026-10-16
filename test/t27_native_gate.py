#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Native t27c gate for the SKY26d spec: parse, typecheck, test-report, seal --save, seal --verify,
and a negative control of the native compiler.

This runs the REAL t27c (built from a pinned t27 commit) with a verified Zig on PATH. It is fail
closed, it never passes a seal over missing evidence, and it does not assume what the native test
runner measures. Facts about the tool (read from t27 bootstrap/src, not guessed):
  * `test-report SPEC` prints `tests N`, `pass N`, `FAIL N` (counts of the spec's `test` blocks),
    `BLOCKED` when no binary was produced; exit 0 green, 1 a test FAILS, 2 BLOCKED.
  * `seal --save` writes `tests` = {total, passed, failed, failing, forced, vacuous}; `vacuous` is an
    INTEGER: the number of passing tests that executed zero RUNTIME asserts (asserts that Zig folds at
    compile time are not counted). It is NOT a boolean.
Therefore: `vacuous` is validated as a non-negative integer <= total and REPORTED as `vacuous of total`;
a high count is not an error by itself, because compile-time assertions are checked by the compiler. What
must hold instead is the negative control below: the same spec with ONE existing assertion made false
must be detected by the native toolchain.

Gate (any failure = non-zero exit):
  1. parse exit 0; typecheck prints `Typecheck OK (0 errors, 0 warnings)`.
  2. test-report: not BLOCKED, tests >= EXPECT_MIN_TESTS, pass == tests, FAIL == 0. These native
     counts (23 `test` blocks) are independent of, and are not the same thing as, the 112 assertions
     executed by test/t27_exec.py nor the runtime-assert counters of the native runner.
  3. seal --save exit 0; seal JSON: no `blocked`, total >= EXPECT_MIN_TESTS, failed == 0,
     passed == total, forced is not true, vacuous is an int, four real gen_hash_* values.
  4. seal --verify exit 0 and `all hashes MATCH`.
  5. NEGATIVE CONTROL, for each of two real assertions (one over constants only, one over a function
     call): a copy of the spec with that single assertion replaced by a false one must still parse
     and typecheck (these do not evaluate asserts), and `test-report` on it must be DETECTED:
       - exit 1 with FAIL >= 1 and no BLOCKED (the test runner caught it), OR
       - exit 2 BLOCKED whose reason is `does not compile` AND names `assertion failed` (the message of
         the generated Zig `@compileError("assertion failed")`, checked in the Zig that t27c emitted
         for this spec). Any other compile failure (missing import, type error, linker) is NOT
         accepted: it would be an infrastructure problem hiding behind a BLOCKED verdict.
     Exit 0 / all tests pass, a missing Zig, a crash, or any other outcome fails the gate: the native
     path would then be unable to see a wrong spec.
Never `--force`; T27C_TEST_REPORT_EXIT_ZERO must be unset. Outputs of every step, the seal JSON and
`native-summary.json` are written into --out for upload. The seal is NOT committed automatically.

Usage (repo root): python3 test/t27_native_gate.py --t27c PATH --spec specs/numeric/gf16_dot4_sky26d.t27 --out DIR
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

EXPECT_MIN_TESTS = 23   # the spec has 23 `test` blocks; invariants are separate
# (label, exact original line, replacement); each original must occur exactly once in the spec
MUTATIONS = [
    ("false_const_assert",
     "    assert(UIO_OE_ALL == 0);",
     "    assert(UIO_OE_ALL == 1);"),
    ("false_call_assert",
     "    assert(dot4_rise(1, 0) == 1);",
     "    assert(dot4_rise(1, 0) == 0);"),
]
errors = []
summary = {}


def fail(msg):
    errors.append(msg)
    print("NATIVE_GATE FAIL:", msg)


def run(step, cmd, out_dir, env):
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    with open(os.path.join(out_dir, step + ".log"), "w") as f:
        f.write("$ " + " ".join(cmd) + "\n--- exit %d ---\n--- stdout ---\n%s\n--- stderr ---\n%s\n"
                % (p.returncode, p.stdout, p.stderr))
    print("%-34s exit %d" % (step, p.returncode))
    return p


def counts(out):
    nums = {}
    for key in ("tests", "pass", "FAIL"):
        m = re.search(r"^\s*%s\s+(\d+)\s*$" % key, out, re.M)
        nums[key] = int(m.group(1)) if m else None
    return nums


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t27c", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not os.access(args.t27c, os.X_OK):
        fail("t27c is not executable: %s" % args.t27c)
        return 1
    if os.environ.get("T27C_TEST_REPORT_EXIT_ZERO"):
        fail("T27C_TEST_REPORT_EXIT_ZERO is set; it would turn a red test run green")
        return 1
    zig = shutil.which("zig")
    if not zig:
        fail("zig is not on PATH (the test runner needs it; sealing without it is refused)")
        return 1
    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ)
    env.pop("T27C_TEST_REPORT_EXIT_ZERO", None)
    ver = subprocess.run([zig, "version"], capture_output=True, text=True).stdout.strip()
    print("zig on PATH:", zig, ver)
    summary["zig"] = ver
    with open(os.path.join(args.out, "toolchain.txt"), "w") as f:
        f.write("zig %s at %s\nt27c %s\n" % (ver, zig, args.t27c))

    # 1. parse, typecheck
    p = run("parse", [args.t27c, "parse", args.spec], args.out, env)
    if p.returncode != 0 or not p.stdout.strip():
        fail("parse exit %d or empty output" % p.returncode)
    p = run("typecheck", [args.t27c, "typecheck", args.spec], args.out, env)
    if p.returncode != 0 or not re.search(r"Typecheck OK \(0 errors, 0 warnings\)", p.stdout + p.stderr):
        fail("typecheck exit %d or not '0 errors, 0 warnings'" % p.returncode)

    # 2. test-report
    p = run("test-report", [args.t27c, "test-report", args.spec, "--specs-dir", "specs", "--verbose"],
            args.out, env)
    out = p.stdout
    if p.returncode != 0:
        fail("test-report exit %d (1 = a test FAILS, 2 = BLOCKED)" % p.returncode)
    if "BLOCKED" in out:
        fail("test-report is BLOCKED: blocked is not a pass")
    nums = counts(out)
    print("test-report counts:", nums)
    summary["test_report"] = nums
    if nums["tests"] is None or nums["tests"] < EXPECT_MIN_TESTS:
        fail("test-report tests=%s, need >= %d (zero or missing results are not a pass)"
             % (nums["tests"], EXPECT_MIN_TESTS))
    if nums["pass"] != nums["tests"] or nums["FAIL"] != 0:
        fail("test-report pass=%s tests=%s FAIL=%s" % (nums["pass"], nums["tests"], nums["FAIL"]))
    m = re.search(r"vacuous passes\s+(\d+) of (\d+)", out)
    summary["runtime_assert_vacuous_line"] = m.group(0) if m else None
    print("runtime-assert line:", summary["runtime_assert_vacuous_line"])

    # 3. seal --save
    p = run("seal-save", [args.t27c, "seal", args.spec, "--save"], args.out, env)
    seal_path = None
    m = re.search(r"Seal saved to (\S+)", p.stdout)
    if p.returncode != 0 or not m:
        fail("seal --save exit %d or no 'Seal saved to' line (a refused seal is a failure, never --force)"
             % p.returncode)
    else:
        seal_path = m.group(1)
        if not os.path.isfile(seal_path):
            fail("seal file %s does not exist" % seal_path)
            seal_path = None
    if seal_path:
        shutil.copy(seal_path, os.path.join(args.out, os.path.basename(seal_path)))
        with open(seal_path) as f:
            seal = json.load(f)
        tests = seal.get("tests", {})
        summary["seal_tests"] = tests
        if "blocked" in tests:
            fail("seal tests record is blocked: %s" % tests.get("blocked"))
        total = tests.get("total")
        if not isinstance(total, int) or total < EXPECT_MIN_TESTS or tests.get("failed") != 0 \
                or tests.get("passed") != total:
            fail("seal tests record is not a full pass: %s" % tests)
        if tests.get("forced") is True:
            fail("seal tests record is forced: %s" % tests)
        vac = tests.get("vacuous")
        # `vacuous` is an integer count (tests that ran zero RUNTIME asserts), never a bool.
        if isinstance(vac, bool) or not isinstance(vac, int) or vac < 0 \
                or (isinstance(total, int) and vac > total):
            fail("seal tests.vacuous is %r (type %s): expected a non-negative integer <= total"
                 % (vac, type(vac).__name__))
        else:
            print("seal tests: total=%s passed=%s failed=%s vacuous=%d of %s "
                  "(vacuous = passes with 0 runtime asserts; compile-time asserts are checked by the compiler)"
                  % (total, tests.get("passed"), tests.get("failed"), vac, total))
        for k in ("gen_hash_zig", "gen_hash_verilog", "gen_hash_c", "gen_hash_rust"):
            if seal.get(k) in (None, "", "none", "missing"):
                fail("seal %s is %r" % (k, seal.get(k)))
        print("seal built_by / zig:", seal.get("built_by"), seal.get("toolchain", {}).get("zig"))

    # 4. seal --verify
    p = run("seal-verify", [args.t27c, "seal", args.spec, "--verify"], args.out, env)
    if p.returncode != 0 or "all hashes MATCH" not in p.stdout:
        fail("seal --verify exit %d or not 'all hashes MATCH'" % p.returncode)

    # 5. negative control of the native compiler (never sealed)
    with open(args.spec) as f:
        spec_text = f.read()
    negdir = os.path.join(args.out, "negctl")
    os.makedirs(negdir, exist_ok=True)
    summary["negative_control"] = {}
    for label, old, new in MUTATIONS:
        if spec_text.count(old) != 1:
            fail("negative control %s: line %r found %d times in the spec (must be exactly 1)"
                 % (label, old, spec_text.count(old)))
            continue
        mpath = os.path.join(negdir, label + ".t27")
        with open(mpath, "w") as f:
            f.write(spec_text.replace(old, new))
        pp = run("neg-%s-parse" % label, [args.t27c, "parse", mpath], args.out, env)
        pt = run("neg-%s-typecheck" % label, [args.t27c, "typecheck", mpath], args.out, env)
        if pp.returncode != 0 or pt.returncode != 0:
            fail("negative control %s: the mutated spec must still parse and typecheck "
                 "(parse %d, typecheck %d)" % (label, pp.returncode, pt.returncode))
        pr = run("neg-%s-test-report" % label, [args.t27c, "test-report", mpath, "--specs-dir", "specs"],
                 args.out, env)
        n = counts(pr.stdout)
        blocked = "BLOCKED" in pr.stdout
        if pr.returncode == 1 and not blocked and (n["FAIL"] or 0) >= 1:
            verdict = "detected by the test runner (FAIL %d)" % n["FAIL"]
        elif pr.returncode == 2 and blocked and "does not compile" in pr.stdout \
                and "assertion failed" in pr.stdout and "zig not on PATH" not in pr.stdout:
            # the t27 Zig backend emits @compileError("assertion failed") for a false assertion that
            # is evaluated at compile time (and @panic("assertion failed") at run time); any other
            # compile error (missing import, type error, linker) is an infrastructure problem
            verdict = "rejected by the Zig compiler at compile time (BLOCKED: assertion failed)"
        else:
            verdict = None
            fail("negative control %s NOT detected: test-report exit %d, counts %s, blocked=%s. "
                 "The native path would not see a false assertion." % (label, pr.returncode, n, blocked))
        summary["negative_control"][label] = {"exit": pr.returncode, "counts": n, "blocked": blocked,
                                              "verdict": verdict}
        print("negative control %-20s -> %s" % (label, verdict))

    with open(os.path.join(args.out, "native-summary.json"), "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    if errors:
        print("NATIVE_GATE FAIL: %d problem(s); logs in %s" % (len(errors), args.out))
        return 1
    print("NATIVE_GATE PASS: native parse, typecheck, test-report (%d/%d, 0 FAIL), seal --save and --verify, "
          "negative controls detected" % (nums["pass"], nums["tests"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
