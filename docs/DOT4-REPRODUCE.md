# Reproducing every check of the SKY26d GF16 DOT4

Contract: `specs/numeric/gf16_dot4_sky26d.t27` (issue #100). Run all commands from the repository
root. Nothing here proves fit in one tile or the 50 MHz clock: only the GDS run of the final commit does.

Tools: `iverilog` + `vvp`, `yosys`, `verilator` (only for the archived width gate), Python 3, and a
virtualenv with `cocotb==2.0.1` (`pip install -r test/requirements.txt`). The Python helpers use only
the standard library.

## 0. Run one heavy job at a time (local) - CI is the place for the heavy runs
The unit sweeps and the gate-level run are heavy. LOCALLY run at most one heavy job at a time, check the
machine load first (memory pressure, swap), use `--jobs 1`, and do not start a heavy job while the machine
is under pressure; the full sweeps and the gate-level run belong to the remote CI (a fork push), where
`--jobs 4` uses the 4 cores of the GitHub runner. The commands below use `--jobs 1` locally; the CI file
uses `--jobs 4`. The coverage of the full profile is not reduced for either.

## 1. Static: spec, oracle, source list (seconds)
```
T27C=/Users/ssdm4/Documents/t27/target/release/t27c      # path of the local t27c build
$T27C parse     specs/numeric/gf16_dot4_sky26d.t27       # syntax
$T27C typecheck specs/numeric/gf16_dot4_sky26d.t27       # types ("0 errors, 0 warnings")
python3 test/t27_exec.py             # executes all 23 tests + 5 invariants (fail closed)
python3 test/oracle_selfcheck.py     # oracle vs code-space derivation, conformance JSON, spec vs model
python3 test/check_project.py        # info.yaml == Makefile == instantiated files; pins; action pin; no '*'
```
`t27c parse` and `typecheck` do not evaluate `assert`. `test/t27_exec.py` does, for this one spec, by
transpiling the small .t27 subset it uses; it is an honest equivalent, not the t27 seal.
Seal: use the repository's `t27c seal` procedure only after parse and typecheck pass, and only with the
`t27c` subcommand and flags shown by `$T27C --help`; do not hand-write seal files.

## 2. Synthesis audit (about a minute)
```
python3 test/synth_audit.py          # yosys: no $mul/$div/$mod/$pow; synth -flatten succeeds
```

## 3. Isolated units against the exact oracle
```
python3 test/unit_sweeps.py --profile quick --jobs 1     # local: sampled, NOT exhaustive
python3 test/unit_sweeps.py --profile full  --jobs 1     # exhaustive; very long locally, prefer CI
python3 test/unit_sweeps.py --profile full  --jobs 4     # what the GitHub job runs (4 cores, 240 min limit)
```
`--jobs N` is the number of worker processes (each runs the Python oracle and one `vvp`); it changes the
time, never the coverage. The CI job `unit-full` has a 240-minute limit.
Also runs `test/tb_sig_mul10.v`: all 2^20 products of the shift-and-add multiplier against the integer
product. `full` = ADD: all 63 exponent differences x both sign relations x all 512x512 mantissa pairs
(33,030,144 vectors); MUL: all 512x512 mantissa pairs for the 33 exponent sums around the threshold
values, stride 7 for the other sums; both: boundary sweeps and a zero/Inf/NaN class sweep. The count of
vectors is printed; a `quick` run says so. Required: 0 mismatches.

## 4. Pin-level tests on the RTL
```
source /Users/ssdm4/trinity-results/toolchains/phi-sky26d-venv/bin/activate
cd test && PHI_RANDOM_N=300 make && cd ..                 # 14 cocotb tests
python3 test/check_results.py test/results.xml test/test.py
```
`PHI_RANDOM_N` is the number of random vectors (CI uses 2000). Waves only with `make PLUSARGS=+vcd`.

## 5. Negative controls (mutated RTL must FAIL)
```
python3 test/mutation_runner.py       # inside the cocotb venv; copies src/ and test/ to a temp dir
```
It first requires the unmodified RTL to pass all tests, then applies 11 mutations to the temporary
copy (B forced to 0, wrong `uio_oe`, signature shown as a result, result visible while busy, ERR never
set, WR/GO priority swapped, `ena` ignored, GO not resetting PTR, wrong MUL tie, wrong ADD tie, no sticky
bit) and requires every one to be KILLED by a PINFAIL; a surviving mutation or an infrastructure error
fails the run.

## 6. Gate level
The same `test/test.py` runs on the gate-level netlist in CI (`gl_test` of the official action, which
installs `cocotb==2.0.1` from `test/requirements.txt` and runs `make -B GATES=yes`). Locally, after a
GDS build produced `test/gate_level_netlist.v`:
`cd test && make -B GATES=yes` (needs `PDK_ROOT`), then `check_results.py` as above.

## 7. Same commit, official actions
`gds.yaml` (GDS, precheck, gl_test, viewer) and `docs.yaml` use the official `@ttsky26d` actions pinned
to commit `395eedb3eeec3c2f4961c3b04ff2cf87f5525636`. The physical checks `gds`, `precheck` and `gl_test`
(and the Docs build, and every job of `test.yaml`) are REQUIRED and must be green on the SAME final commit;
download the artifacts and record their SHA256.

The `viewer` job only publishes the layout view to GitHub Pages. It is optional and is not a physical
check: it runs only on the default branch of the repository that has Pages configured, and it is
SKIPPED on every other branch and on a fork (no `continue-on-error`, nothing masked). A skipped viewer
is not a physical pass and not a failure; a green `gds` + `precheck` + `gl_test` is what counts.

## Archived comparison jobs
`test/gf16_v2_verify.py`, `test/width_gate.py`, `test/gf_add_sweep.py`, `test/phi_audit.py` test archived
files and stay in CI as they were; their result is not a statement about the current DOT4. The old
`gf16_v2_verify.py` only checks that the v2 units are faithful (<= 1 ULP add, <= 0.5 ULP mul).
