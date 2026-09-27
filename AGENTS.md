# AGENTS.md

Guidance for AI agents working in this repository. For user-facing documentation
(architecture, memory map, host tooling usage, build/flash instructions), see
[`README.md`](README.md) — this file only covers things that aren't obvious from
reading the source, and that caused real mistakes during a 2026-09 restructuring
pass (branch `refactor/build-layout-cleanup`).

## Directory map

```
soc/                    Veryl RTL: soc_top + peripherals directly here,
  cpu/                  CPU core submodules
  uart/                 UART controller submodules
firmware/               Cargo workspace (virtual manifest)
  boot_manager/         Boot Manager crate (package name: boot_manager)
  resident_loader/      Resident Loader crate (package name: resident_loader)
hack_demo/              Standalone Hack 16-bit C/asm demo app (toolchain self-test)
zephyr_workspace/       Zephyr west module: board/SoC/driver/dts support for "vux9k"
  app/rust_demo/        Shared Rust app core, dual-backend (see "rust_demo" below)
sim/                    cocotb/pytest RTL testbenches
  unit/                 cocotb unit tests (single RTL module each)
  integration/          cocotb SoC-level integration tests + sdcard_model.py/virtual_serial.py
  emulator/             pytest Python-emulator tests + emulator.py
scripts/                Build/CI plumbing only (elf2bin.py, run_riscv_tests.py, ...)
tools/                  End-user CLI: vux_tool.py (UART flashing/diagnostics/monitor)
vendor/                 bc_clone_rs submodule; riscv-tests (fetched on demand)
build/                  ALL generated/build output (gitignored) — see below
```

Everything under `soc/`, `firmware/`, `hack_demo/`, `zephyr_workspace/`, `scripts/`,
`tools/` is source. Nothing generated should ever be written outside `build/`
except the handful of firmware-hex symlinks described below.

## `build/` — unified generated output

Every build artifact (Veryl `.sv`/`.map` output, firmware `.bin`/`.hex`, Hack
firmware, riscv-tests ISA test files, Zephyr's `west build` output, synthesis/PnR/
STA/bitstream files, cocotb builds and runs under `build/sim/`) lands under `build/<category>/`.
Run `veryl build --out-dir build/veryl` (or `make veryl`) and it mirrors the
source tree exactly — `build/veryl/soc/cpu/*.sv`, `build/veryl/soc/uart/*.sv`, etc.
See the `Makefile`'s `BUILD_DIR`/`VERYL_OUT_DIR`/`FIRMWARE_BUILD_DIR`/`SYNTH_DIR`
variables for the exact layout.

**Exception:** `firmware.hex` and `firmware_d0-3.hex` also exist as symlinks at
the repo root, pointing into `build/firmware/`. This is required
because `soc/soc_ram.veryl`'s `$readmemh("firmware.hex", ...)` calls use a bare
filename resolved relative to whatever directory the invoking tool's process cwd
is (yosys/nextpnr: repo root). cocotb runs don't use them: `sim/runners/sim_runner.py`
puts the same symlinks into each test's own run directory, its cwd. `$readmemh` is **not**
simulation-only, it's how the actual FPGA bitstream gets the boot firmware baked
into BRAM at synthesis time. Don't remove these symlinks or "clean up" the
duplication without also fixing the underlying `$readmemh` calls (out of scope
for a build-layout change; would require re-verifying real hardware).

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
`make sim-unit` (Icarus) and `yosys -p "read_verilog -sv …"` catch all four in
seconds; `veryl build` alone does not.

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

## Veryl module resolution is directory-agnostic

`veryl build`/`veryl check` scan the whole project root recursively for `.veryl`
files and resolve module names (via `inst`) in one flat global namespace — there
is no `import`/`use`/`include` syntax. Moving `.veryl` files between directories
never breaks Veryl itself; only external tooling that hardcodes paths to the
*generated* `.sv` output (Makefile `read_verilog` commands, `sim/runners/sim_runner.py`'s
`RTL_SOURCES`, `scripts/run_riscv_tests.py`) needs updating.

## `sim/` is split by test kind, not by module under test

`sim/unit/`, `sim/integration/`, `sim/emulator/` hold, respectively: cocotb tests
against a single RTL module, cocotb tests against the full `soc_top`, and pytest
tests against the Python software emulator (`emulator.py`). This split (done in
a 2026-09 `sim/` reorg pass) tracks *which helper module a test needs*, not
directory conventions — `sdcard_model.py`/`virtual_serial.py` only ever get
imported by `sim/integration/` tests, `emulator.py` only by `sim/emulator/`
tests, so each helper lives alongside its only consumers. `sim/runners/` holds
the pytest entry point that builds and runs all cocotb tests (see below).
`gowin_cells_sim.veryl`, `tb_soc_top.sv`, and `tb_hex_runner.veryl` stay at `sim/`'s
top level — **do not move them into a subdirectory**:
- `sim/runners/sim_runner.py` refers to `sim/tb_soc_top.sv` (RTL and GLS) by path.
- The two `.veryl` files are the only Veryl sources outside `soc/`; Veryl mirrors
  the source tree into `build/veryl/`, so they compile to
  `build/veryl/sim/gowin_cells_sim.sv` (the Gowin cell models for GLS, a path
  `sim/runners/sim_runner.py` hardcodes) and `build/veryl/sim/tb_hex_runner.sv`
  (a path `scripts/run_riscv_tests.py` hardcodes). Moving either `.veryl` file
  changes that generated path and silently breaks GLS or `make test-isa` (this is
  the same class of landmine described in "Veryl module resolution is
  directory-agnostic" above, but easy to miss since they look like ordinary test
  helpers, not RTL source).

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
  `sim/emulator`: `sim/unit`/`sim/integration` contain cocotb modules named
  `test_*.py` that pytest must never collect directly.

The pytest-based files in `sim/emulator/` rely on pytest's *implicit* same-directory
`sys.path` insertion (no `conftest.py`/`pytest.ini` backs this) to find
`emulator.py` — keep any new pytest-based emulator test in that same directory.
Any test file that computes `REPO_ROOT` via `dirname(dirname(__file__))` (used
to reach `build/`/`tools/` from a `sim/<file>.py` that's one level below repo
root) needed an extra `dirname()` after the move to `sim/<subdir>/<file>.py`
(two levels below repo root) — check this if you add a new such computation.

## Cargo workspace gotcha: rustflags paths are workspace-root-relative

`firmware/` is a Cargo workspace (`boot_manager` + `resident_loader` members).
**When you build a member from within its own directory (`cd firmware/boot_manager
&& cargo build`), rustc/the linker still runs with cwd = the workspace root
(`firmware/`), not the member directory.** This means each member's
`.cargo/config.toml` `-C link-arg=-T<path>` linker-script flag must be written
relative to `firmware/`, e.g. `-Tboot_manager/bootstrap/link.x`, **not**
`-Tbootstrap/link.x`. Getting this wrong produces `rust-lld: error: cannot find
linker script` with no indication of *why* the cwd changed. Both members' output
binaries land in the single shared `firmware/target/riscv32i-unknown-none-elf/
release/{boot_manager,resident_loader}`.

## `rust_demo`: two swappable backends for one shared core

`zephyr_workspace/app/rust_demo/src/lib.rs`'s `rust_main()` is Zephyr-agnostic
Rust application logic that only calls `extern "C"` functions declared in
`zephyr_ffi.rs` (`vux9k_print_str`, `vux9k_k_msleep`, ...). Two different things
provide the concrete implementation of those C functions:

- `src/bin/standalone.rs` — a `#![no_std] #![no_main]` bare-metal RV32I binary
  with its own `_start`/UART MMIO, **no Zephyr kernel involved at all**. This is
  what actually gets built (`make zephyr-rust-lib`) and flashed to real hardware
  (`scripts/test_hardware.py`'s Slot 1 fallback). Its banner text ("Hello from
  Rust running on Zephyr RTOS!", printed by the shared `rust_main()`) is
  therefore **inaccurate** when running this path — it's boilerplate shared with
  the Zephyr backend below, not a bug in the build.
- `zephyr_workspace/app/src/main.c` + `CMakeLists.txt` — a real Zephyr-RTOS
  backed implementation (`printk`, `k_msleep`) meant to link `rust_demo` as a
  staticlib into an actual Zephyr app. **This is currently not built by anything**
  — `make build-zephyr` builds `vendor/bc_clone_rs/examples/zephyr_app`
  (`BC_APP_DIR` in the `Makefile`) instead, which is a *different*, working
  example app that already correctly uses this repo's `zephyr_workspace` as a
  west module (`BOARD_ROOT`/`SOC_ROOT`/`EXTRA_ZEPHYR_MODULES`). Don't delete
  `zephyr_workspace/app/src/main.c` thinking it's dead code from a grep-only
  reachability check — it's an intentional (if currently unexercised) reference
  for wiring `rust_demo` into a real Zephyr app; confirmed by tracing the
  `extern "C"` call graph, not just Makefile reachability.

If you actually need to test the Zephyr-backed path, you'd have to add a new
`west build` invocation pointing at `zephyr_workspace/app` (or extend
`vendor/bc_clone_rs/examples/zephyr_app` to link `libvux9k_rust_demo.a`) — this
hasn't been done, don't assume it works without testing on real hardware.

## `make test-isa`: riscv-tests on `tb_hex_runner`

`scripts/run_riscv_tests.py` builds riscv-tests (`rv32ui`/`rv32mi`, `env/p`, both
pinned and fetched into `vendor/riscv-tests/`) and runs each on
`sim/tb_hex_runner.veryl`. It replaced a riscv-arch-test (ACT4) harness that could
never fail: its halt address (`0x1000`) sat *inside* the test code, so every test
"passed" after executing its first ~4 KB, and ACT4's self-checking needs expected
signatures from the Sail reference model, which were never generated. Things that
are easy to break without noticing:
- **Results travel through `tohost`, not the console.** `scripts/riscv_tests/link.ld`
  places `.tohost` at `0xF000_0000` (NOLOAD, outside the tb's 256 KB RAM window);
  the tb treats a store there as the verdict (`1` = PASS, `(TESTNUM<<1)|1` = FAIL).
  Keep the address in the linker script and the tb's `TOHOST_ADDR` in sync, and
  never let it alias into RAM.
- **Verdicts go through the CPU's trap path.** Upstream `env/p` reports PASS/FAIL
  via `ecall` -> `trap_vector` -> `write_tohost`, so a broken ECALL/MRET shows up
  as a harness self-test failure (typically a timeout), not as quietly green
  tests. From `02a105a` until the trap path was restored, a local
  `riscv_test.h` override sidestepped this; don't bring one back to paper over
  a trap regression.
- **`EXPECTED_FAILURES` is strict.** Known gaps are listed with reasons
  (`rv32ui-p-ma_data`: misaligned accesses trap rather than being done in
  hardware, and `env/p` has no handler to emulate them; `rv32mi-p-pmpaddr`: no
  PMP); a listed test that
  starts passing is reported as XPASS and fails the run — remove the entry
  when fixing the CPU rather than loosening the check.
- **The harness self-test must stay first.** `scripts/riscv_tests/selftest_fail.S`
  deliberately fails test case 2, and the run aborts unless it's reported as
  exactly `FAIL (TESTNUM=2)`. This is what proves the verdict plumbing works;
  don't remove it to "speed things up".
- **The CPU is M-mode only, with WARL CSRs the `rv32mi` tests depend on.**
  `mstatus` keeps only MIE/MPIE and reads MPP as M (`illegal`/`scall` write MPP
  and read it back to detect S/U-mode; a fully writable `mstatus` makes them take
  the S-mode path and fail), `mtvec` is direct-mode only, `misa` is read-only
  (so `ma_fetch` skips its RVC part). Unimplemented CSR addresses read 0 and
  ignore writes instead of trapping (firmware/Zephyr read e.g. `mhartid`); only
  a write to a read-only CSR (`addr[11:10] == 3`, e.g. `unimp`) is illegal.
- **The tb is split: hardware in Veryl, stimulus in cocotb.** `sim/tb_hex_runner.veryl`
  holds the RAM (preloaded from `program.hex` in the cwd), the CPU and the `tohost`
  latch; `scripts/riscv_tests/hex_runner.py` drives clock/reset, waits for the latch
  with one trigger (not per cycle) and writes `verdict.txt`. `run_riscv_tests.py
  rv32ui-p-add ...` runs a subset; set `TRACE=1` in the environment for a
  per-cycle PC/instruction log in `build/riscv_tests/runs/<test>/sim.log`.

## SoC cocotb tests: clock in HDL, never wait with `ClockCycles`

Simulation speed of the long `soc_top` tests is dominated by per-clock Python/VPI
work, not by the design, so two rules keep them fast (measured 2026-09 on Icarus
RTL, `sim-soc-fast`: 491 s -> 194 s):
- **The clock comes from `sim/tb_soc_top.sv`**, a pass-through wrapper that
  generates 27 MHz in HDL; the SoC is `dut.soc` inside it. Driving the clock
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

`make` runs short tests on Icarus (`SIM_UNIT`, compile time dominates) and the
long SoC/GLS runs on Verilator (`SIM_SOC`): `sim-soc-fast`, `sim-hw-flow`,
`sim-soc-gls-fast`, `sim-gls-hw-flow`. Measured 2026-09 (wall time incl. compile):
hw-flow RTL 860 s (Icarus) -> ~30 s; gls-fast ~1,050 s -> ~33 s. Verilator needs
`perl` (not on the Silverblue host — run it inside the `fedora-toolbox-43` toolbox).
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

### Synthesis flags (`SYNTH_GOWIN_OPTS = -nowidelut -no-rw-check`)

Both were measured on the same RTL (all 5 seeds, 2026-09): median slack -1.73 ns
without them, +2.03 ns with `-nowidelut`, +4.78 ns with both.
- `-nowidelut` keeps abc9 from packing wide muxes into MUX2_LUT5..8. Without it
  the mapping swung by hundreds of LUTs (and several ns) on two-line RTL
  changes, while cosmetic edits (renames, comments) changed nothing — so a
  per-commit timing comparison was mostly noise. With it LUT counts track the
  RTL. Behavior-neutral.
- `-no-rw-check` drops yosys' read/write collision emulation for block RAM: ~79
  flops, 67 of them for `i_mem`, whose comparator took the combinational fetch
  address (`pc_out`, i.e. `next_pc`) onto the critical path. The one collision
  this SoC can produce is a store to the next instruction's I-RAM word (the
  fetch reads that word in the store's MEM_WAIT cycle); its fetch is now
  undefined, documented as unsupported in README ("Writing code into I-RAM").
  RTL simulation still shows read-before-write, so no RTL test can see this.
Don't drop either flag to "simplify" the flow; re-measure with `make timing`
if you change them.

### `make timing`: one comparable record per RTL commit

`make timing` synthesizes, routes *every* seed (`run_pnr.py --all-seeds`: no early
stop, not even when a seed closes) and prints
cell counts (LUT/MUX2/FF/ALU/BSRAM/SSRAM), per-seed slack, best and median, and
the worst path's start/end flops; it also appends a row to
`build/timing/history.tsv`. Run it after every RTL commit of the timing work:
seeds alone move slack by ~1.7 ns, so compare best *and* median, and treat LUT
count as the steadier signal. Register names don't survive synthesis
(`synth_gowin` ends with `autoname`), so end points are shown as the flop's
`src` (generated `.sv` line range of its `always_ff`) plus the stem of its D
net's autoname (e.g. `D=instr_addr` is the PC register). `make sta` prints the
same end points for the adopted seed.

## Code coverage: `make coverage` (Verilator line + toggle)

`HDL_COVERAGE=1` makes `sim_runner.py` build Verilator RTL sims with
`--coverage-line --coverage-toggle` into their own `build/sim/verilator-cov/` tree
(so normal builds are untouched); each test writes `coverage.dat` into its run
directory. **Not `COVERAGE`:** cocotb 2.0 still reads that (deprecated) name as
"collect Python coverage of the testbench", which needs the `coverage` pip package —
not in CI's dependencies, so every test failed there with "coverage module not
available" while passing in a dev venv that happened to have it. `make coverage` runs every RTL unit + SoC test that way (incl. the slow
hw-flow), merges them with `verilator_coverage` into `build/coverage/merged.dat`
(+ `merged.info` for lcov) and annotates the generated `.sv` into
`build/coverage/annotated/`. When reading the annotation:
- Points are per hierarchy (unit-test toplevel vs `tb_soc_top.soc.cpu_inst`), so a
  `~` (partial) line is often covered by another instance; `%` lines were reached by
  nothing at all. `--annotate-points` shows every point with its `hier=`.
- Not measured: Icarus-only runs (`make test-isa`'s `tb_hex_runner`) and GLS. RV32I
  load/ALU/misaligned-fetch lines in `unified_cpu` that riscv-tests exercise
  therefore show as `%` — cross-check against `test-isa` before calling them holes.

## Step 3 commit rule: refactors are proven with `make eqy`, timing changes are tested

Keep every RTL commit one of two kinds, never both:
- **Behavior-preserving refactor** (renames, restructuring, dead-code removal,
  expression rewrites): must pass `make eqy EQY_BASE=<parent> EQY_TOP=unified_cpu`
  and `EQY_TOP=soc_top`. That is a proof over all inputs, not a test.
- **Behavior-changing timing work** (pipelining, extra wait states, anything that
  changes cycle-level behavior): verified with `make test-sim` plus the GLS runs.
Mixing them loses the ability to tell which half caused a regression.

`scripts/run_eqy.py` builds the base commit's RTL from a `git archive` in a temp
directory *outside* the repo (Veryl scans the whole project root, so a copy inside
it would clash), caches it as `build/eqy/gold-<sha>/`, snapshots both sides into
`build/eqy/<top>/` and runs Yosys `eqy` (sby/bitwuzla, `memory_map`), with both
sides flattened below `<top>` (~6 min per top on 4 cores). Notes:
- Because of the flattening, a refactor may change submodule ports or move logic
  between submodules; only `<top>`'s own ports have to match.
- `soc_ram` is replaced by a ports-only stub on both sides (its `$readmemh` needs
  `firmware.hex`; its RAMs are too big to prove usefully), so changes *inside*
  `soc_ram` are not covered — and neither is a change to its *ports*: the stubs'
  interfaces must match, so such a change is its own commit, verified by tests.
- eqy pairs up gold/gate signals by name before proving anything. When a change
  renames or restructures too much, it fails at that stage ("conflicting matches
  ... Failed to partition design") rather than with a counterexample — e.g. the
  trap-restore commit against its parent. Split such a refactor into smaller
  steps (or add `[match]` hints) rather than skipping the check.
- A mismatch is reported per partition ("Failed to prove equivalence of
  partition unified_cpu.hack_jump_take"); details are under
  `build/eqy/<top>/<top>/`.
- eqy runs with `[options] insbuf off`. By default eqy chains a buffer between
  every alias name of a net before partitioning, so each name can be a cut point;
  the chain follows the netlist's connection order, which differs between gold and
  gate as soon as a refactor adds a consumer to a net (typically: extracting logic
  into a new instance that reads `rf_rs1_data`/`rv_imm`). One design then split
  names the other kept together — "conflicting matches for gold bit …" at
  partitioning, or false mismatches in whole groups (`reg_file.rs1_data.*`,
  `rv_decode.imm_reg.*`). With it off, all names of a net stay one net on both
  sides; R3–R5's extractions prove equivalent with no hand-written exclusions.
  `EQY_NOMATCH="<patterns>"` still adds `gold-/gate-nomatch` lines if ever needed
  (excluding names only removes cut points: partitions grow, the proof stays sound).
- Proofs are local to each partition, so a change that is only unreachable
  because of how *another* block drives it (e.g. `fifo_sync` accepting a write
  on an empty-and-read cycle, which `uart_controller` never produces) still
  fails. Such a change is a behavior change as far as this workflow goes: test it. A partition that is truly equivalent but needs
  deeper induction than `--depth` (5) can also fail: raise the depth before
  concluding the refactor changed behavior.

## Known pre-existing (structure-independent) failures

Confirmed present on `main` too (reproduced in a clean `git worktree`), not
caused by any restructuring:
- `make sim-hack-pytest` (`sim/emulator/test_hack_firmware.py`, 5 tests) — the
  Python software emulator doesn't reach the firmware's PASS banners. **This is
  an emulator bug, not a firmware one:** the same `build/hack/firmware.hex` on
  the RTL (`make sim-hack-rtl`, `test_soc_hack`) prints the complete report up to
  `ALL HACK C FIRMWARE TESTS PASSED (100%)!` (verified 2026-09).
- `make sim-zephyr-repl` (`sim/emulator/test_soc_bc.py`, 3 of 4 tests) — the
  first REPL round-trip after boot passes, but every subsequent interactive
  `feed_input()`/`run()` round-trip returns empty output instead of the
  expected `bc` result; likely an emulator UART/REPL-loop interaction bug
  (input not being re-delivered, or the emulator's output buffer being cleared
  before the SoC has actually produced it), unrelated to file layout. Confirmed
  by reproducing on a clean `git worktree` of `main` reusing the same
  `build/zephyr/zephyr/zephyr.bin` (2026-09).
- `make sta` — nextpnr timing closure at 30 MHz fails for every seed in
  `PNR_SEEDS` (-4.1 to -4.7 ns after the trap path was restored, 2026-09); this is
  the timing-closure work of step 3, not a build-system bug.

## PnR seeds: `scripts/run_pnr.py` adopts one seed and records it

`make pnr`/`sta`/`bitstream` run nextpnr once per seed in `PNR_SEEDS`, in parallel,
each into `build/synth/pnr/seed_<N>/` (`soc_pnr.json`, `soc_sta.json`,
`nextpnr.log`). The first seed to meet timing stops the others and is adopted;
a seed finishing below `PNR_ABORT_SLACK` (-1.5 ns) stops them too, since seed
jitter can't close that gap. Without a closing seed, the best finished one is
adopted. Its results are copied to `build/synth/soc_{pnr,sta}.json` and the seed
is recorded in `build/synth/pnr_seed.json`, which `make sta` prints — so
`pack.fs` and the STA report always come from one known seed. Changing
`PNR_SEEDS` re-runs PnR (stamp file); `PNR_ABORT_SLACK=none` keeps going past
hopeless seeds (but still stops at the first closing one); `make timing` routes
every seed. The seed list
is simply the first few primes: a seed only initializes nextpnr's RNG, so the
point is a fixed, documented list, not any property of the values.

## Test tiers: what `test-sim` does and doesn't cover

`make test-sim` (every push/PR in CI) runs the unit suites (RTL + GLS), `test-isa`,
and the SoC tests: `sim-soc-fast` (+ `-icarus`), `sim-soc-mmio`, `sim-boot` (CLI),
`sim-hack-rtl`, `sim-sd-quirks`, `sim-hw-flow` (RTL flashing flow) and
`sim-soc-gls-fast`. `make test-slow` (CI nightly + manual `workflow_dispatch`) adds
`sim-gls-hw-flow` and `sim-hw-flow-icarus`. Until 2026-09 the flashing flow ran in
no tier at all (25–35 min RTL / many hours GLS on Icarus); on Verilator it's ~16 s
RTL / ~3–6 min GLS, which is what made it affordable per PR.

`test_soc_hardware_flow.py` is the only test that drives the full multi-step UART
flashing protocol end-to-end through `tools/vux_tool.py`'s `build_vux9_image()`:
`'w'`-command flash of a Hack and a RISC-V payload (slot-ID handshake →
sector-count handshake → per-sector streaming), a byte-exact check of what
reached the SD model, boot-header verification via `s1`, and booting the flashed
payload through the Resident Loader. The other CLI commands are
`test_soc_boot.py`'s job.

Also run `sim-gls-hw-flow` (not in `test-sim`) explicitly whenever you touch:
- the UART flashing handshake (`firmware/boot_manager/src/main.rs`'s
  `write_sectors_from_uart()` and its `[READY]`/`[READY-SLOT:N]`/
  `[READY-COUNT:N]`/`[READY-SEC:i]` protocol),
- `tools/vux_tool.py`'s `build_vux9_image()` or the sector/header layout it
  produces,
- the SD boot/slot-loading chain (`resident_loader`, `slot_sector()`), or
- anything under `sim/integration/` that talks to the virtual UART/SD models
  (`sdcard_model.py`, `virtual_serial.py`) — a change there can silently
  desync `test_soc_hardware_flow.py`'s own protocol assumptions from the
  firmware's actual behavior (this happened during the 2026-09 `sim/` reorg:
  the test's handshake had drifted from a 2-step to a 3-step protocol
  sometime after the VUX9 v3 boot header work, undetected until this test was
  actually run — `sim-soc-fast`'s lighter flow never exercises the `'w'`
  command at all).

It's also worth running once as a final check after any `sim/` directory
layout change (as opposed to content change), even though the fast tests all
pass, precisely because it is the one test exercising the longest real
UART/SD interaction chain and is therefore most likely to surface a subtle
path- or timing-related regression the fast tests can't reach.

## Verification checklist after touching build paths or directory layout

Run in this order (each depends on the previous succeeding):
```
make firmware        # Cargo workspace build -> build/firmware/
make sim-unit         # broadest RTL path coverage (20 unit test modules)
make test-isa
make build-hack        # hack_demo/
make sim-hack-rtl
make sim-hw-flow         # full UART/SD chain; longest real interaction (Verilator, ~16 s)
make synth-top          # yosys must resolve build/veryl/soc/{cpu,uart}/*.sv
make zephyr-rust-lib     # rust_demo standalone binary
python3 scripts/check_no_absolute_paths.py
git status                # confirm no stray untracked build output
```
`make sta` and `make test-hw` require real hardware/long PnR runs — skip unless
specifically investigating timing or hardware behavior.
