---
paths:
  - "soc/**/*.veryl"
  - "sim/*.veryl"
  - "soc/verilator_lint.vlt"
---

# Veryl and the generated SystemVerilog

Read before editing `soc/**/*.veryl` or the `.veryl` files in `sim/`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Veryl constructs the toolchain rejects

The generated SV must get through Icarus, Verilator and Yosys. Veryl accepts all of
these; one of the three tools does not (found 2026-09):
- **`return` in a `function`** — Yosys' Verilog frontend has no `return`, so Veryl
  functions are unusable in synthesized RTL. Compute shared logic in an
  `always_comb` into a `var` instead (e.g. `unified_cpu`'s `rv_alu_op`).
- **An enum-typed `if … ? A : B` expression** (`state = if c ? S1 : S2`) — Icarus
  wants an explicit cast ("This assignment requires an explicit cast"). Use an
  `if`/`else` statement.
- **`case` on named constants** (`case x { pkg::CONST: … }`) — Veryl emits
  `case (x) inside`, which Icarus can't parse. Use `switch { x == pkg::CONST: … }`
  (emits `case (1'b1)`), or enum members, which stay a plain `case`.
- **A cast in a `case` expression** (`case x as pkg::Enum { … }`) — Yosys: "Static
  cast with non constant expression". Assign the cast to a `var` first.
- **An enum with an explicit base type** (`enum E: logic<12> { … }`) — Veryl emits
  `$bits(logic [11:0])'(…)` casts that Yosys can't parse. Leaving the type out is
  **not** a fix when the enum is cast *from* a wider value: Veryl sizes an untyped
  enum to its largest member (CSR addresses up to `12'h344` became 10 bits), so
  `csr_addr as E` silently drops the top bits and aliases other addresses. For
  such decodes keep a plain `case` on literals (as `rv32i_csrs` does).
- **A signed comparison through `as i32`** (`(a as i32) <: (b as i32)`) — Veryl emits
  `int'(a) < int'(b)`; Icarus and Verilator compare signed, Yosys treats `int'()` as
  a resize and compares **unsigned**. Nothing fails: the RTL tests pass, and `make eqy`
  reads both sides with Yosys, so it can't see it either. BLT/BGE were built this way
  and the bitstream took the wrong branch whenever the operand signs differed (found
  2026-09 by the Zephyr demo on the board; reproduced in GLS). Write signed compares
  out (`if a[31] != b[31] ? a[31] : a <: b`, as `next_pc_unit`/`rv32i_alu` do);
  `test_rv32i_branches` runs on the netlist too (`make sim-gls-unit`).
`make check-rtl-syntax` (Yosys read + Icarus compile of the generated SoC, about a second)
catches the first five, as does `make sim-unit`; `veryl build` alone does not. Only a GLS test catches the last one.

`$readmemh` in synthesized RTL: `soc_ram` preloads its arrays in a plain Veryl
`initial` block, allowed by `#[allow(initial_assign)]` on each array. Veryl emits it
without a `` `ifndef SYNTHESIS `` guard, so yosys reads the files and the firmware
lands in the bitstream's block-RAM init (the old `embed` needed an
`` `endif ``/`` `ifndef `` trick for that). If you ever touch it, check that the
synthesized BRAM cells still carry non-zero `INIT_RAM_*` parameters.

Veryl emits every port as a variable (`input var logic`), always; there is no
option, and `input tri logic` becomes the invalid `input var tri logic`. Connecting
a *net* to such a port is legal SystemVerilog, but Icarus coerces the port to inout
and drives X. Veryl-to-Veryl hierarchies never hit this (their signals are
variables too), but a synthesized netlist connects only wires, so with
`sim/gowin_cells_sim.veryl` every Icarus GLS test failed (Verilator was fine).
`sim/runners/sim_runner.py` therefore gives GLS builds a copy of the generated cell
models with `input var` rewritten to `input wire`
(`build/sim/gowin_cells_sim_net_inputs.sv`). Keep that step if you touch the GLS
source list.

## `make lint-rtl`: Verilator `-Wall` on the generated `.sv`, zero warnings

`make lint-rtl` (in `test-sim`) runs `verilator --lint-only -Wall` on `board_top` (the
whole SoC as synthesized, with the PLL's cell model), `tb_hex_runner` and `tb_gowin_bram`.
Any warning fails it. Veryl can't emit Verilator comments, so waivers go into
`soc/verilator_lint.vlt`, one `lint_off` per finding with `-file` and a `-match` as narrow
as the message allows, and a comment saying why it's by design. Fix a finding in the
Veryl source instead when the fix is a refactor (`make eqy`); `soc_ram` changes aren't
covered by eqy, so its findings are waived. The simulator builds keep `-Wno-lint`
(`sim_runner.py`'s `BUILD_ARGS`): lint is this target's job, not the tests'.
