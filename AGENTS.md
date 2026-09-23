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
STA/bitstream files, cocotb `sim_build_*/` dirs) lands under `build/<category>/`.
Run `veryl build --out-dir build/veryl` (or `make veryl`) and it mirrors the
source tree exactly — `build/veryl/soc/cpu/*.sv`, `build/veryl/soc/uart/*.sv`, etc.
See the `Makefile`'s `BUILD_DIR`/`VERYL_OUT_DIR`/`FIRMWARE_BUILD_DIR`/`SYNTH_DIR`
variables for the exact layout.

**Exception:** `firmware.hex` and `firmware_d0-3.hex` also exist as symlinks at
the repo root and in `sim/`, pointing into `build/firmware/`. This is required
because `soc/soc_ram.veryl`'s `$readmemh("firmware.hex", ...)` calls use a bare
filename resolved relative to whatever directory the invoking tool's process cwd
is (yosys/nextpnr: repo root; cocotb/icarus: `sim/`) — `$readmemh` is **not**
simulation-only, it's how the actual FPGA bitstream gets the boot firmware baked
into BRAM at synthesis time. Don't remove these symlinks or "clean up" the
duplication without also fixing the underlying `$readmemh` calls (out of scope
for a build-layout change; would require re-verifying real hardware).

## Veryl module resolution is directory-agnostic

`veryl build`/`veryl check` scan the whole project root recursively for `.veryl`
files and resolve module names (via `inst`) in one flat global namespace — there
is no `import`/`use`/`include` syntax. Moving `.veryl` files between directories
never breaks Veryl itself; only external tooling that hardcodes paths to the
*generated* `.sv` output (Makefile `read_verilog` commands, `sim/Makefile`
`VERILOG_SOURCES`, `scripts/run_riscv_tests.py`) needs updating.

## `sim/` is split by test kind, not by module under test

`sim/unit/`, `sim/integration/`, `sim/emulator/` hold, respectively: cocotb tests
against a single RTL module, cocotb tests against the full `soc_top`, and pytest
tests against the Python software emulator (`emulator.py`). This split (done in
a 2026-09 `sim/` reorg pass) tracks *which helper module a test needs*, not
directory conventions — `sdcard_model.py`/`virtual_serial.py` only ever get
imported by `sim/integration/` tests, `emulator.py` only by `sim/emulator/`
tests, so each helper lives alongside its only consumers. `sim/Makefile`,
`gowin_cells_sim.v`, `tb_soc_top.sv`, and `tb_hex_runner.veryl` stay at `sim/`'s
top level — **do not move them into a subdirectory**:
- `sim/Makefile` hardcodes `$(CURDIR)/gowin_cells_sim.v` (GLS) and
  `$(CURDIR)/tb_soc_top.sv` (RTL and GLS) — `$(CURDIR)` means wherever
  `sim/Makefile` itself is invoked from.
- `tb_hex_runner.veryl` is the one `.veryl` file inside `sim/` (everything else
  lives under `soc/`); Veryl mirrors the source tree into `build/veryl/`, so it
  compiles to `build/veryl/sim/tb_hex_runner.sv`, a path `scripts/run_riscv_tests.py`
  hardcodes. Moving the `.veryl` file changes that generated path and silently
  breaks `make test-isa` (this is the same class of landmine described
  in "Veryl module resolution is directory-agnostic" above, but easy to miss
  since `tb_hex_runner.veryl` looks like an ordinary test helper, not RTL source).

cocotb's bare `MODULE=test_xxx` resolution (used by ~30 call sites in the root
`Makefile`'s `$(MAKE) -C sim TOPLEVEL=... MODULE=...` invocations) works across
all three subdirectories because `sim/Makefile` puts all of `unit/`,
`integration/`, and `emulator/` on `PYTHONPATH` — there are no module-name
collisions, so this needed no changes to the 30 call sites themselves. The
pytest-based files in `sim/emulator/` rely on pytest's *implicit* same-directory
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

## `make test-isa`: riscv-tests on `tb_hex_runner`, and why it doesn't use `ecall`

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
- **`scripts/riscv_tests/env/riscv_test.h` overrides `RVTEST_PASS`/`RVTEST_FAIL`**
  (via `#include_next`) to jump straight to `write_tohost` instead of `ecall`.
  Upstream `env/p` reports via `ecall` -> trap handler, but `unified_cpu` currently
  takes **no traps at all**: commit `02a105a` dropped the ECALL/EBREAK/MRET/
  interrupt decode (`trap_entry`/`is_ecall`/`is_mret` are only ever assigned 0).
  Without the override every test would hang instead of reporting. Its `mret`
  into the test body only works because `mepc` happens to point at the very next
  instruction. Once traps are restored, the override can be dropped.
- **`EXPECTED_FAILURES` is strict.** Known gaps (the trap-dependent `rv32mi`
  tests, misaligned access, PMP) are listed with reasons; a listed test that
  starts passing is reported as XPASS and fails the run — remove the entry
  when fixing the CPU rather than loosening the check.
- **The harness self-test must stay first.** `scripts/riscv_tests/selftest_fail.S`
  deliberately fails test case 2, and the run aborts unless it's reported as
  exactly `FAIL (TESTNUM=2)`. This is what proves the verdict plumbing works;
  don't remove it to "speed things up".
- The tb accepts `+TRACE` (per-cycle PC/instruction log) and `+MAX_CYCLES=N`,
  e.g. `vvp -n build/riscv_tests/tb_hex_runner.vvp +HEX_FILE=build/riscv_tests/<test>.hex +TRACE`.

## SoC cocotb tests: clock in HDL, never wait with `ClockCycles`

Simulation speed of the long `soc_top` tests is dominated by per-clock Python/VPI
work, not by the design, so two rules keep them fast (measured 2026-09 on Icarus
RTL, `sim-soc-fast`: 491 s -> 194 s):
- **The clock comes from `sim/tb_soc_top.sv`**, a pass-through wrapper that
  generates 27 MHz in HDL; the SoC is `dut.soc` inside it. Driving the clock
  with `cocotb.clock.Clock` costs a VPI write + callback every half period and
  alone made Icarus ~2.4x slower (7.9k vs 19k cycles/s). Run SoC tests with
  `TOPLEVEL=tb_soc_top` (RTL or `SIM_GLS=1`) and set them up with
  `sim/integration/soc_env.py`'s `start_soc()`, which only falls back to a
  cocotb `Clock` when handed a bare `soc_top`.
- **Long waits use `Timer` or events, not `ClockCycles(clk, n)`** — cocotb 2.0's
  `ClockCycles` is a Python loop awaiting every edge. Use `soc_env.wait_cycles(n)`
  (one Timer) and `VirtualSerialBridge.wait_for(token, timeout_cycles)` (sleeps
  until a byte arrives) instead of polling the UART buffer every bit.

Tests not yet on `soc_env` (`test_soc_boot`/`_hack`/`_rv32i`/`_zephyr`) still drive
their own clock on bare `soc_top`.

## Known pre-existing (structure-independent) failures

Confirmed present on `main` too (reproduced in a clean `git worktree`), not
caused by any restructuring:
- `make sim-hack-pytest` (`sim/emulator/test_hack_firmware.py`, 5 tests) — the
  Python software emulator doesn't reach the firmware's PASS banners; likely an
  emulator/firmware interaction bug, unrelated to file layout.
- `make sim-zephyr-repl` (`sim/emulator/test_soc_bc.py`, 3 of 4 tests) — the
  first REPL round-trip after boot passes, but every subsequent interactive
  `feed_input()`/`run()` round-trip returns empty output instead of the
  expected `bc` result; likely an emulator UART/REPL-loop interaction bug
  (input not being re-delivered, or the emulator's output buffer being cleared
  before the SoC has actually produced it), unrelated to file layout. Confirmed
  by reproducing on a clean `git worktree` of `main` reusing the same
  `build/zephyr/zephyr/zephyr.bin` (2026-09).
- `make sta` — nextpnr timing closure fails (~-2ns slack) across all
  `PNR_SEEDS`; a physical-design marginality issue, not a build-system bug.

## `sim-hw-flow` / `sim-gls-hw-flow`: slow, deliberately excluded from `test-sim`

`make sim-hw-flow` (`sim/integration/test_soc_hardware_flow.py`, RTL) and
`make sim-gls-hw-flow` (same test, `SIM_GLS=1`) are the only tests that drive
the full multi-step UART flashing/boot protocol end-to-end through
`tools/vux_tool.py`'s `build_vux9_image()`: prompt sync, hardware
self-diagnostics, SD sector dump, `'w'`-command flash of both a Hack and a
RISC-V payload (slot-ID handshake → sector-count handshake → per-sector
streaming), boot-header verification, and SD-boot execution. Each run takes
roughly 25–35 minutes for RTL and longer for GLS, which is why `test-sim`/
`test`/`test-ci` use the much lighter `sim-soc-fast`/`sim-soc-gls-fast`
instead (boot + execution only, no flashing protocol, no SD dump/verify
round trip) and never run these two. A green CI (`test-sim`) therefore does
**not** confirm the flashing protocol still works.

Run `sim-hw-flow`/`sim-gls-hw-flow` explicitly whenever you touch:
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
make sim-unit         # broadest RTL path coverage (15 unit tests)
make test-isa
make build-hack        # hack_demo/
make sim-hack-rtl
make synth-top          # yosys must resolve build/veryl/soc/{cpu,uart}/*.sv
make zephyr-rust-lib     # rust_demo standalone binary
python3 scripts/check_no_absolute_paths.py
git status                # confirm no stray untracked build output
```
`make sta` and `make test-hw` require real hardware/long PnR runs — skip unless
specifically investigating timing or hardware behavior.
