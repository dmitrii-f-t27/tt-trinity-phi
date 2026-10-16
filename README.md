# Trinity Phi - pin-loadable GF16 DOT4 for Tiny Tapeout SKY26d

`tt_um_trinity_nano` is a dot product of two 4-element vectors in the 16-bit GF16 float format
(1 sign, 6 exponent, 9 mantissa, bias 31): the host loads A[0..3] and B[0..3] as 16 bytes through
`ui_in`, starts the computation, and reads the 16-bit result a byte at a time from `uo_out`. One shared
multiplier and one shared adder, 8 clocks per dot product; `uio` is input only. Target: one 1x1 tile,
sky130A, 50 MHz.

## Status (read this first)

This is work in progress for TTSKY26d, tracked in issue
[#100](https://github.com/gHashTag/tt-trinity-phi/issues/100). Nothing has been submitted.

- **Written, not yet verified by evidence in this repository:** the RTL, the reference model, the unit
  sweeps, the pin-level tests, the negative controls and the workflows exist in the working tree, but no
  result of running them is recorded here. Passing parse and typecheck of the `.t27` spec is the only
  recorded check, and it does not execute the spec's assertions.
- **Unknown until a GDS run of the final commit:** fit in one 1x1 tile, timing at 50 MHz, area.
- **Not claimed:** performance, energy, TOPS/W, silicon results. No die of this design exists.

## Where things are

| What | Where |
|---|---|
| Normative contract (the project's own language) | [`specs/numeric/gf16_dot4_sky26d.t27`](specs/numeric/gf16_dot4_sky26d.t27) |
| Pin protocol, read map, arithmetic rules | [`docs/info.md`](docs/info.md) |
| How to reproduce every check, exact commands | [`docs/DOT4-REPRODUCE.md`](docs/DOT4-REPRODUCE.md) |
| RTL (4 files, one source list) | [`src/`](src/): `tt_um_trinity_nano.v`, `gf16_v2_mul.v`, `gf16_v2_add.v`, `gf16_sig_mul10.v`; list in [`info.yaml`](info.yaml) |
| Tests, oracle, sweeps, mutation harness | [`test/`](test/) |
| CI | [`.github/workflows/`](.github/workflows/): `test.yaml` (assertions, oracle, units, pin tests, mutations, synthesis audit, archived comparison jobs), `gds.yaml` (official `@ttsky26d` GDS, precheck, gate-level test, optional viewer), `docs.yaml` |
| Task and decisions | issue [#100](https://github.com/gHashTag/tt-trinity-phi/issues/100) |
| Original README of the earlier revisions (archival) | [`docs/archive/README-pre-SKY26d.md`](docs/archive/README-pre-SKY26d.md) |

Earlier blocks (Lucas POST, HWRNG, restraint controller, ROMs, mesh, quantizers, power modules, other
formats) remain in `src/` and `docs/` as history. They are not part of the current top module, and the claims
of the earlier README (canonical `0x47C0` on the pins after reset, TOPS/W figures, "die-unique" nonce) do
not apply to the current design.

## Contract in one paragraph

Rounding: multiply to nearest with ties toward zero; add exact round-to-nearest-even; no subnormals
(`E=0, M>0` is an ordinary number, `E=0, M=0` is zero); every arithmetic zero result is `+0`; NaN is
`0xFE01`. Order: `acc = MUL0; acc = ADD(acc, MUL1); acc = ADD(acc, MUL2); r = ADD(acc, MUL3)`, each
operation rounded to GF16, no fused multiply-add. Details and the exact pin protocol: `docs/info.md` and the
`.t27` spec.

## License and credit

Apache-2.0 (see [LICENSE](LICENSE)). Original project by Dmitrii Vasilev; the DOT4 rewrite is by Dmitrii
Fedorov ([@dmitrii-f-t27](https://github.com/dmitrii-f-t27)).
