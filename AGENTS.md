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
scripts/                Build/CI plumbing only (elf2bin.py, run_arch_test.py, ...)
tools/                  End-user CLI: vux_tool.py (UART flashing/diagnostics/monitor)
vendor/                 bc_clone_rs submodule; riscv-arch-test (fetched on demand)
build/                  ALL generated/build output (gitignored) — see below
```

Everything under `soc/`, `firmware/`, `hack_demo/`, `zephyr_workspace/`, `scripts/`,
`tools/` is source. Nothing generated should ever be written outside `build/`
except the handful of firmware-hex symlinks described below.

## `build/` — unified generated output

Every build artifact (Veryl `.sv`/`.map` output, firmware `.bin`/`.hex`, Hack
firmware, arch-compliance test files, Zephyr's `west build` output, synthesis/PnR/
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
`VERILOG_SOURCES`, `scripts/run_arch_test.py`) needs updating.

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

## Known pre-existing (structure-independent) failures

Confirmed present on `main` too (reproduced in a clean `git worktree`), not
caused by any restructuring:
- `make sim-hack-pytest` (`sim/test_hack_firmware.py`, 5 tests) — the Python
  software emulator doesn't reach the firmware's PASS banners; likely an
  emulator/firmware interaction bug, unrelated to file layout.
- `make sta` — nextpnr timing closure fails (~-2ns slack) across all
  `PNR_SEEDS`; a physical-design marginality issue, not a build-system bug.

## Verification checklist after touching build paths or directory layout

Run in this order (each depends on the previous succeeding):
```
make firmware        # Cargo workspace build -> build/firmware/
make sim-unit         # broadest RTL path coverage (15 unit tests)
make test-arch-compliance
make build-hack        # hack_demo/
make sim-hack-rtl
make synth-top          # yosys must resolve build/veryl/soc/{cpu,uart}/*.sv
make zephyr-rust-lib     # rust_demo standalone binary
python3 scripts/check_no_absolute_paths.py
git status                # confirm no stray untracked build output
```
`make sta` and `make test-hw` require real hardware/long PnR runs — skip unless
specifically investigating timing or hardware behavior.
