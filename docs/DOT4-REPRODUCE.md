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
It first requires the unmodified RTL to pass all tests, then applies 15 mutations (the 11 original ones plus four for the explicit write enables, the readback group select, the staged alignment sticky and a too-low far-alignment threshold; a mutation that cannot change the GF16 result, such as turning the far jam 1 into 0, is deliberately NOT included because an equivalent mutant cannot be killed) to the temporary
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
check: it runs only on the default branch (`main`) of whichever repository runs the workflow and is SKIPPED on
every other branch (no `continue-on-error`, nothing masked). On the `main` of a fork it does run, and
whether Pages is configured there is not verified. A skipped viewer is not a physical pass and not a
failure; a green `gds` + `precheck` + `gl_test` is what counts.

## 8. Native t27 toolchain gate (CI only; local build not required)
Job `t27-native` in `test.yaml` runs the REAL `t27c`, not the Python adapter. It checks out the pinned t27
commit `32c25e8798e33e03d5f6e7aee876df8629008a49` into `t27-src/` (a separate folder), builds
`cargo build --release -p t27c --locked`, fetches Zig 0.16.0 with `python3 test/fetch_zig.py --version 0.16.0
--dest "$RUNNER_TEMP/zig" --path-file "$GITHUB_PATH"` (verifies size and SHA-256 against
`https://ziglang.org/download/index.json`, prints the verified version and digest, changes PATH for that job
only), then runs `python3 test/t27_native_gate.py --t27c t27-src/target/release/t27c --spec
specs/numeric/gf16_dot4_sky26d.t27 --out native-t27`, which executes, from this repository's root:
`t27c parse SPEC`, `t27c typecheck SPEC`, `t27c test-report SPEC --specs-dir specs --verbose`,
`t27c seal SPEC --save`, `t27c seal SPEC --verify`. `--force` is never used and
`T27C_TEST_REPORT_EXIT_ZERO` must be unset. A BLOCKED run, zero tests, any failing test, a refused seal, a
forced test record, or a `gen_hash` of `none` fails the job. The seal's `tests.vacuous` is an INTEGER (the
number of passing tests that executed zero RUNTIME asserts; compile-time assertions are checked by the Zig
compiler instead), so it is validated as a non-negative integer and reported as `vacuous of total`, not treated
as a flag. What must hold instead is a negative control of the native toolchain: two copies of the spec, each
with ONE existing assertion made false, must still parse and typecheck, and `test-report` on each must either
FAIL (exit 1, FAIL >= 1) or be rejected by the compiler with the specific message of a false assertion
(BLOCKED, `does not compile` AND `assertion failed`, the text of the generated `@compileError`); any other
compile failure (missing import, type, linker), a pass, a crash or a missing Zig fails the gate. The native counts (23 `test` blocks) and the runtime-assert counters are different
from the 112 assertions executed by `test/t27_exec.py`; they are reported separately and are not added up. The native outputs and the seal JSON are
uploaded as the artifact `t27-native-gate`; the seal is NOT committed automatically; a person reviews the
artifact first. `python3 test/t27_exec.py` (28 blocks) remains a separate cross-check, not the seal.

### Seal origin metadata (test runner witness)
`t27c seal` records `toolchain.test_runner` as the sha256 of the file `bootstrap/src/test_report.rs` read
RELATIVE TO THE WORKING DIRECTORY (t27 `main.rs`, `seal_toolchain`), and `missing` when that file is absent;
it also refuses a spec that is outside the working directory (`seal_spec_path`), so the seal must run from this
repository's root. `t27_native_gate.py --t27-src t27-src --t27-commit 32c25e87...` therefore checks that the
t27 checkout is at the pinned commit and that its `bootstrap/src/test_report.rs` is byte-identical to the git
blob at that commit (the `t27c` that runs was built from the same commit), makes that real file visible at the
relative path for the seal run, requires `seal.toolchain.test_runner` to equal `sha256:<that digest>`, and
removes the temporary copy afterwards. The seal JSON is never edited by hand and `--force` is never used.

## 9a. Hierarchical area diagnosis (CI, after Build GDS)
`gds.yaml` runs `python3 test/area_profile.py --out area-profile` after the official Build GDS step with
`if: always()` and uploads the artifact `AREA_PROFILE`. It installs the host `yosys` (a different version from the
one inside the GDS flow; recorded), finds exactly one non-empty `sky130_fd_sc_hd__tt_025C_1v80.lib` on the runner,
fetches archived commit `949a634` with `git fetch --depth=1 origin 949a634...` (no checkout, the branch is
untouched) and synthesises, with the SAME recipe (`synth -flatten`, `dffunmap`, `dfflibmap`, `abc -liberty`,
`stat -liberty`) and the same liberty: the carry-save multiplier alone; the multiplier with the hidden-1 bits
constant; the adder of 949a634 and of the current commit; the full top of 949a634 and of the current commit;
and the top with MUL and ADD as non-constant BLACKBOXES (storage, multiplexers and control only; the blackbox
area is excluded and labelled so). Outputs: `AREA_PROFILE.md`, `area-profile.json`, `STATUS.txt`, per-profile
logs, final `stat` reports (`*.stat.txt`) and `.ys` scripts, `commands.txt`, the liberty path and sha256. Only
the final `stat` report is parsed. In the full and arithmetic profiles ANY cell of unknown area or unmapped cell
makes that profile FAIL with no number; in the storage profile the only allowed unknown-area cells are exactly the
blackboxes `gf16_v2_add` and `gf16_v2_mul` (explicitly excluded); anything else is FAIL. Any incomplete
diagnosis makes the step exit non-zero (the artifact upload runs with `if: always()`); the step can never turn a
red Build GDS green. It is a diagnosis (host yosys, not the GDS flow's version), not a fit proof; whether the
liberty and tools exist on the runner is only confirmed by the CI run itself.

## 9. Datasheet preview (CI)
After the official docs action, job `docs-preview` in `docs.yaml` downloads the artifact `PDF`, requires a
non-empty `datasheet.pdf`, runs `pdfinfo` and `pdftoppm -r 110 -png` for every page, requires one non-empty
PNG per page and uploads the artifact `DocsPreview` (page images and `pdfinfo.txt`) for a visual check. It does
not replace or weaken the official docs job; a local render is not needed.

## Archived comparison jobs
`test/gf16_v2_verify.py`, `test/width_gate.py`, `test/gf_add_sweep.py`, `test/phi_audit.py` test archived
files and stay in CI as they were; their result is not a statement about the current DOT4. The old
`gf16_v2_verify.py` only checks that the v2 units are faithful (<= 1 ULP add, <= 0.5 ULP mul).
