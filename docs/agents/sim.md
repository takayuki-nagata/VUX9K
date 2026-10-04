# Simulation tests (cocotb, Verilator)

Read before adding or changing tests under `sim/` or the runner. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## `sim/` is split by test kind, not by module under test

`sim/unit/`, `sim/integration/`, `sim/emu/` hold, respectively: cocotb tests against a
single RTL module, cocotb tests against the full `soc_top`, and pytest tests against
the Rust emulator. The split tracks *which helper a test needs*: `sdcard_model.py`/
`virtual_serial.py`/`soc_env.py` are for `sim/integration/` (cocotb), `vux9k.py` for
`sim/emu/`. Pure-Python helpers both sides share (`rv32_asm.py`, `sdcard_protocol.py`,
`lockstep_programs.py`) live in `sim/integration/`; `sim/emu/vux9k.py` puts that
directory on `sys.path`. `sim/runners/` holds the pytest entry point that builds and
runs all cocotb tests (see below).
`gowin_cells_sim.veryl`, `tb_soc_top.sv`, `tb_hex_runner.veryl` and `tb_gowin_bram.veryl`
stay at `sim/`'s top level — **do not move them into a subdirectory**:
- `sim/runners/sim_runner.py` refers to `sim/tb_soc_top.sv` (RTL and GLS) by path.
- The `.veryl` files there are the only Veryl sources outside `soc/`; Veryl mirrors
  the source tree into `build/veryl/`, so they compile to
  `build/veryl/sim/gowin_cells_sim.sv` (the Gowin cell models for GLS) and
  `build/veryl/sim/tb_gowin_bram.sv` (a test wrapper for the SP/SDPB models), paths
  `sim/runners/sim_runner.py` hardcodes, and `build/veryl/sim/tb_hex_runner.sv`
  (a path `scripts/run_riscv_tests.py` hardcodes). Moving any of them
  changes that generated path and silently breaks GLS or `make test-isa` (this is
  the same class of landmine described in layout.md, "Veryl module resolution is
  directory-agnostic", but easy to miss since they look like ordinary test
  helpers, not RTL source).
- `tb_soc_top.sv` is the one hand-written SystemVerilog file, kept on purpose: its
  HDL clock (see "SoC cocotb tests" below) can't be written in Veryl, whose only
  time-based clock (`$tb::clock_gen`) exists just in `#[test]` modules run by
  Veryl's own simulator. Its header has the measurement behind keeping it.

## cocotb tests run through pytest + `cocotb_tools.runner`, not a Makefile

Every cocotb test module is one parametrized case in `sim/runners/test_sim.py`
(`test_unit`, `test_unit_gls`, `test_soc`, `test_soc_gls`, keyed by module
name); the root `Makefile`'s `sim-*` targets just select node IDs, e.g.
`pytest -s "sim/runners/test_sim.py::test_soc[test_soc_fast]"`. `SIM=icarus|verilator`
selects the simulator (the Makefile sets it per target from `SIM_UNIT`/`SIM_SOC`, see
the Verilator section below). `sim/runners/sim_runner.py` owns the source lists and
layout: one compiled build per `build/sim/<sim>[-gls]/<toplevel>/` (compile is
`flock`-serialized), and a separate run directory + results file per test
module under it, so any tests can run concurrently. Non-obvious bits:
- **cocotb test modules are imported by bare name** inside the simulator, from
  `PYTHONPATH` — which the runner builds from the *pytest process's* `sys.path`
  (and it ignores an inherited `PYTHONPATH`), so `sim_runner.run()` inserts
  `sim/unit` and `sim/integration` into `sys.path` itself. Module names must stay
  unique across both directories.
- **`LD_PRELOAD` of `librt.so.1`/`libutil.so.1` is required** for oss-cad-suite's
  `vvp` to load the uv-built libpython ("librt.so.1: cannot open shared object
  file"); the runner adds it. Don't set `PYTHONHOME` — it trips cocotb's
  "unexpected sys.executable" check.
- `pyproject.toml` restricts pytest's `testpaths` to `sim/runners` and
  `sim/emu`: `sim/unit`/`sim/integration` contain cocotb modules named
  `test_*.py` that pytest must never collect directly.

The tests in `sim/emu/` import `vux9k` (same directory, pytest's implicit `sys.path`
insertion), which finds the `vux9k_emu` module in `build/emu/python/` (`make emu-py`).
Any test file that computes `REPO_ROOT` via `dirname(dirname(__file__))` (used
to reach `build/`/`tools/` from a `sim/<file>.py` that's one level below repo
root) needed an extra `dirname()` after the move to `sim/<subdir>/<file>.py`
(two levels below repo root) — check this if you add a new such computation.

`make check` runs mypy (`[tool.mypy]` in `pyproject.toml`, `check_untyped_defs`) over
`sim/`, `tools/` and `scripts/`. None of it is a package: modules find each other through
`sys.path` inserts, and `mypy_path` lists the same directories, so a new directory that
tests put on `sys.path` goes there too. Import `vux_tool` as `tools.vux_tool` (with the
repo root on `sys.path`) everywhere; a second spelling (`import vux_tool` from `tools/`)
makes mypy see the file twice under two module names.

## Unit tests: reference models, seeds, and combinational outputs

Each module under `sim/unit/` has, besides its directed cases, a test that drives
boundary values and random stimulus against a Python model (shared operand generators
and models: `sim/unit/unit_models.py`; e.g. `alu_model`, `HACK_COMP`); sequential
modules are checked every cycle. When a check fails, decide from the README/spec
whether the model or the RTL is wrong; an RTL bug gets `xfail(strict=True)` until fixed.
- **Seeds.** cocotb seeds `random` per test from `COCOTB_RANDOM_SEED` and logs it.
  `sim_runner.py` fixes it (`DEFAULT_RANDOM_SEED`) unless the environment sets it, so
  `test-sim` is reproducible; `make sim-unit-random` (test-slow) uses the date. Rerun a
  failure with the logged seed. Run a new random test with a few seeds before
  committing: on its first multi-seed run here, two of them had stimulus outside the
  contract (an SD MISO delay over `(divider - 2)` clocks) or a setup race that one seed
  happened to hide.
- **Don't trigger on edges of combinational outputs.** Icarus shows the zero-width
  glitches of an `always_comb` output (default assignment, then the real one) as VPI
  edges: a monitor on `RisingEdge(dut.rdy)` of `uart_rx` saw duplicate frames, and
  `FallingEdge(dut.busy)` of `uart_tx` fired mid-frame. Sample such signals at a clock
  edge (registered outputs, like `txd`, are safe to follow with `Edge`).
- **A test that waits on the DUT in a loop gets `timeout_time`** on its `@cocotb.test`
  (UART RX/TX, FIFO): a broken DUT, or a mutant (`make mutation`), would otherwise hang
  the run with the free-running clock.
- **Parameters aren't VPI-visible under Verilator** (`make coverage` runs the unit
  tests there): use the module's default value in the test, not `dut.PARAM`.
- **Functional coverage** (cocotb-coverage, `sim/unit/fcov.py`) counts which cases of the
  spec the stimulus reached: the random tests of the ALU, decoder, LSU, next-PC, trap
  unit, CSRs, FIFO, UART RX and SD SPI sample cover points from the function that
  applies one stimulus, and export `fcov.yml` at the end of each test. `make
  coverage-fcov` (test-sim) checks each group against `coverage/thresholds.toml`'s
  `[fcov]` on the fixed seed. A bin random stimulus rarely hits gets a directed case at
  the start of the test (the CSR writes, SD dividers, UART bytes 0x00/0xFF), never a
  lower minimum; a bin that can't happen is a model error (remove it, say why). Sampling
  functions take positional arguments only.

## SoC cocotb tests: clock in HDL, never wait with `ClockCycles`

Simulation speed of the long `soc_top` tests is dominated by per-clock Python/VPI
work, not by the design, so two rules keep them fast (measured 2026-09 on Icarus
RTL, `sim-soc-fast`: 491 s -> 194 s):
- **The clock comes from `sim/tb_soc_top.sv`**, a pass-through wrapper that
  generates the 18 MHz SoC clock in HDL; the SoC is `dut.soc` inside it (`soc_top`
  in RTL builds, the `board_top` netlist in GLS builds, see clock-and-board.md, "The SoC clock"). Driving the clock
  with `cocotb.clock.Clock` costs a VPI write + callback every half period and
  alone made Icarus ~2.4x slower (7.9k vs 19k cycles/s). Run SoC tests with
  toplevel `tb_soc_top` (RTL and GLS; see `SOC`/`SOC_GLS` in `sim/runners/test_sim.py`)
  and set them up with
  `sim/integration/soc_env.py`'s `start_soc()`, which only falls back to a
  cocotb `Clock` when handed a bare `soc_top`.
- **Long waits use `Timer` or events, not `ClockCycles(clk, n)`** — cocotb 2.0's
  `ClockCycles` is a Python loop awaiting every edge. Use `soc_env.wait_cycles(n)`
  (one Timer) and `VirtualSerialBridge.wait_for(token, timeout_cycles)` (sleeps
  until a byte arrives) instead of polling the UART buffer every bit.

All SoC tests are on `soc_env`/`tb_soc_top`; keep new ones there too.

## Verilator: which tests use it, and the `--public-flat-rw` trap

`make` runs short tests on Icarus (`SIM_UNIT`, compile time dominates; also
`test-isa`) and the long SoC/GLS runs on Verilator (`SIM_SOC`): `sim-soc-fast`,
`sim-hw-flow`, `sim-soc-gls-fast`, `sim-gls-hw-flow`, `test-isa-gls`. Measured 2026-09 (wall time incl. compile):
hw-flow RTL 860 s (Icarus) -> ~30 s; gls-fast ~1,050 s -> ~33 s. Verilator needs
`perl`; on a host without it, run Verilator from a container or toolbox that has it.
- **Don't let cocotb's `--public-flat-rw` back in.** cocotb's Verilator runner
  adds it unconditionally; it makes every signal VPI-visible and blocks most of
  Verilator's optimization — on the gate-level netlist it was ~18x slower to
  simulate and ~5x slower to compile. `sim_runner.py` strips it and generates a
  `public.vlt` exposing only the toplevel's own signals, plus `SOC_RTL_PUBLIC`
  (`soc_top.*`, `unified_cpu.pc_out`, `soc_ram.i_mem`) for RTL SoC builds. **A test
  that starts touching another internal signal fails on Verilator with "no such
  attribute" until you add it to `SOC_RTL_PUBLIC`** (Icarus sees everything, so
  it won't catch this).
- **2-state vs 4-state.** Verilator has no X: an undriven input reads 0. This
  already exposed `test_shift_registers` never driving `rst` (it passed on Icarus
  only because X skipped the reset branch). The reverse also holds — Verilator
  can hide a missing reset that Icarus would show as X — which is why
  `test-sim` also runs `sim-soc-fast-icarus`.
- `tb_soc_top.sv`'s `#`-delay clock needs `--timing` (in `BUILD_ARGS`).
- The runner infers the HDL language from the *last* source, so the `.vlt` is
  prepended, not appended.
