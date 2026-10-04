# AGENTS.md

Guidance for AI agents working in this repository. For user-facing documentation
(architecture, memory map, host tooling usage, build/flash instructions), see
[`README.md`](README.md) — this file only covers things that aren't obvious from
reading the source, and that caused real mistakes during a 2026-09 restructuring
pass (branch `refactor/build-layout-cleanup`).

## Directory map

```
soc/                    Veryl RTL: board_top (PLL), soc_top + peripherals directly here,
  cpu/                  CPU core submodules
  uart/                 UART controller submodules
firmware/               Cargo workspace (virtual manifest)
  boot_manager/         Boot Manager crate (package name: boot_manager)
  resident_loader/      Resident Loader crate (package name: resident_loader)
  fw_common/            Boot Manager logic without MMIO, host-tested (make test-fw-host)
  hw_test/              Board self-test in place of the Boot Manager (make hw-smoke)
hack_demo/              Standalone Hack 16-bit C/asm demo app (toolchain self-test)
zephyr_workspace/       Zephyr west module: board/SoC/driver/dts support for "vux9k"
  app/                  Zephyr Rust demo (C glue + rust_demo staticlib; see "Zephyr" below)
sim/                    cocotb/pytest RTL testbenches
  unit/                 cocotb unit tests (single RTL module each)
  integration/          cocotb SoC-level integration tests + sdcard_model.py/virtual_serial.py
  emu/                  pytest tests on the Rust emulator + vux9k.py helpers
  sd_transcripts/       SD exchanges both card models must reproduce
emu/                    Rust emulator (core, CLI vux9k-emu, pyo3 module vux9k_emu)
coverage/               thresholds.toml (make coverage-fw)
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
- **A signed comparison through `as i32`** (`(a as i32) <: (b as i32)`) — Veryl emits
  `int'(a) < int'(b)`; Icarus and Verilator compare signed, Yosys treats `int'()` as
  a resize and compares **unsigned**. Nothing fails: the RTL tests pass, and `make eqy`
  reads both sides with Yosys, so it can't see it either. BLT/BGE were built this way
  and the bitstream took the wrong branch whenever the operand signs differed (found
  2026-09 by the Zephyr demo on the board; reproduced in GLS). Write signed compares
  out (`if a[31] != b[31] ? a[31] : a <: b`, as `next_pc_unit`/`rv32i_alu` do);
  `test_rv32i_branches` runs on the netlist too (`make sim-gls-unit`).
`make sim-unit` (Icarus) and `yosys -p "read_verilog -sv …"` catch the first five in
seconds; `veryl build` alone does not. Only a GLS test catches the last one.

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

## The SoC clock: 18 MHz behind a PLL, and a passing STA is not proof

The board's crystal is 27 MHz; the SoC runs at 18 MHz (`soc_pkg::CLK_HZ`) from the rPLL in
`soc/board_top.veryl`, the synthesis top (README, "Clock"). Found 2026-09: the 27 MHz
bitstreams passed nextpnr's STA on every seed (post-route Fmax 33-39 MHz) and failed on
the board on every seed; with the placement and routing held fixed and only the PLL
divider changed, the same placements passed a CPU self-test at 24 MHz and below. GLS and
eqy can't see this (zero-delay models, and both sides of eqy come from Yosys). So:
- **Don't raise the clock on the strength of STA.** `STA_FREQ` (27 MHz, 1.5x) is a
  guard band, not a guarantee; a faster clock needs the same fixed-placement test on the
  board across several seeds. `make timing` then `make hw-smoke` is that test at the
  current clock: `hw_test` on each routed seed with only the block-RAM contents
  replaced (`scripts/hw_smoke.py`; it refuses to patch when the routed netlist doesn't
  hold `firmware.hex` under the known I-RAM mapping). Run it after RTL changes, before
  flashing a new bitstream. If you change `hw_test`'s tests, take the new checksums
  from the emulator (`sim/emu/test_hw_test.py` prints them on failure).
- **`CLK_HZ` has copies that must change with it**, none derived from `soc_pkg`
  automatically: the emulator (`uart::BIT`, `sdspi::HALF_PERIOD`/`CLK_HZ`, the CLI's real-time
  pacing), `fw_common::map` (`TICKS_PER_*`, `SD_DIV_INIT`), the Boot Manager (`timer.rs` ticks per us/ms, `read_uart_byte_timeout`, the
  `t` banner and expected tick count), the Resident Loader's 10 us CS delay, Zephyr
  (`timebase-frequency`/`clock-frequency` in `vux9k-common.dtsi`,
  `SYS_CLOCK_HW_CYCLES_PER_SEC`), and the test constants (`tb_soc_top.sv`'s half period,
  `soc_env.py`, `virtual_serial.py`, `lockstep_programs.UART_BIT`, `sim/emu/vux9k.py`,
  `test_sdcard_spi.CLK_DIV_HALF`, `test_sd_transcripts.SLOW_HZ`/`FAST_HZ`, the tick windows in `test_soc_boot.py` and
  `test_boot_manager.py`). `make sim-lockstep` catches an emulator that disagrees with
  the RTL; the rest only shows up as timeouts or wrong tick counts.
- **Simulations bypass the PLL.** RTL tests instantiate `soc_top` and drive it at 18 MHz.
  The GLS netlist is `board_top`'s: `tb_soc_top.sv` wraps it when `sim_runner.py`
  defines `VUX9K_GLS`, and `sim/gowin_cells_sim.veryl`'s `rPLL` passes CLKIN straight
  through with LOCK = 1, so the testbench clock is the SoC clock there too. That model
  declares only the parameters `board_top` sets; setting another one makes GLS fail to
  elaborate until the model declares it.

## Board UART output that stops: check the USB hub before the firmware

The board's USB-UART bridge (BL702, full speed) can lose output because of the host's
USB setup, typically another full-speed device busy on the same USB 2.0 hub (found
2026-10). The loss is deterministic, not random: after a short pass-through, the bridge
keeps the first 128 B, drops the rest, and delivers the 128 B seconds later. A timing
change in the firmware can therefore look exactly like a regression: the Resident
Loader's check pass (`c70d180`) only delayed the demo's output, and `make test-hw` failed
tests 14/15 until the board had a hub to itself.
- **Before suspecting the SoC or firmware for lost board output, check the USB topology**
  (`lsusb -t`): give the board its own port or hub.
- **Tell device from host with the LEDs**, not the UART: write progress to the GPIO LED
  register (`0x4000_3000`; software writes 1 = lit, the RTL inverts the pins). A
  `putc` that waits on TX-full can't finish with a stopped transmitter, so a program
  that reaches its last LED step has sent every byte.
- Numbered lines of one length, sent with known gaps and read with arrival timestamps,
  show in one run which output the host path drops or delays.

## Veryl module resolution is directory-agnostic

`veryl build`/`veryl check` scan the whole project root recursively for `.veryl`
files and resolve module names (via `inst`) in one flat global namespace — there
is no `import`/`use`/`include` syntax. Moving `.veryl` files between directories
never breaks Veryl itself; only external tooling that hardcodes paths to the
*generated* `.sv` output (Makefile `read_verilog` commands, `sim/runners/sim_runner.py`'s
`RTL_SOURCES`, `scripts/run_riscv_tests.py`) needs updating.

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
  the same class of landmine described in "Veryl module resolution is
  directory-agnostic" above, but easy to miss since they look like ordinary test
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

## Cargo workspace gotcha: rustflags paths are workspace-root-relative

`firmware/` is a Cargo workspace (`boot_manager` + `resident_loader` members, plus
`fw_common`: the firmware's hardware-independent logic, no MMIO, tested on the host
with `cargo test -p fw_common --target <host triple>` — `make test-fw-host`. The Resident
Loader takes only constants and `crc32_update` from it: it has ~100 bytes left of its
2 KB, which `make firmware-size` enforces along with the Boot Manager's 14 KB).
**When you build a member from within its own directory (`cd firmware/boot_manager
&& cargo build`), rustc/the linker still runs with cwd = the workspace root
(`firmware/`), not the member directory.** This means each member's
`.cargo/config.toml` `-C link-arg=-T<path>` linker-script flag must be written
relative to `firmware/`, e.g. `-Tboot_manager/bootstrap/link.x`, **not**
`-Tbootstrap/link.x`. Getting this wrong produces `rust-lld: error: cannot find
linker script` with no indication of *why* the cwd changed. Both members' output
binaries land in the single shared `firmware/target/riscv32i-unknown-none-elf/
release/{boot_manager,resident_loader}`.

## Zephyr: two boards, and what runs on which

`zephyr_workspace/boards/vux9k/` defines two HWMv2 targets sharing `vux9k-common.dtsi`
(18 MHz, `vux9k,uart`, and the SoC timer as `andestech,machine-timer`, whose
mtime +0x0 / mtimecmp +0x8 layout is `timer_core`'s, so Zephyr's in-tree
`riscv_machine_timer` driver serves it):
- `vux9k`: the real board. flash = I-RAM 0x0000-0x37FF (the Resident Loader owns
  0x3800+), sram = D-RAM less the mailbox words at 0x1FF8/0x1FFC. The linker enforces
  both, so an app that outgrows the board fails to link.
- `vux9k/vux9k/ext`: 512 KB/256 KB, **emulator only** (`vux9k-emu --profile extended`).
  bc_clone_rs (`make build-zephyr`, ~260 KB) runs only here; never present it as
  running on hardware.

`zephyr_workspace/app/` is the Rust demo: Zephyr (`src/main.c`, FFI wrappers for
`printk`/`k_msleep`/`k_uptime_get_32`) calls `rust_main()` from the `rust_demo`
staticlib. `make build-zephyr-demo` builds it for `vux9k` into `build/zephyr-demo/`
(cargo output included); it is what `scripts/test_hardware.py` flashes to Slot 1, and
it is tested from SD on the emulator (`sim/emu/test_zephyr_demo.py`) and the RTL
(`sim-zephyr-demo-rtl`). Its `prj.conf` size settings are what make it fit in 14 KB.

Two traps found while bringing Zephyr up on the RTL (the old Python emulator hid both):
- **The SoC must select Zifencei.** The Zephyr SDK has no libgcc multilib for
  `rv32i_zicsr`; GCC silently links a default one built with RV32M, and the first
  64-bit division (`__udivdi3` -> `divu`) is an illegal instruction.
- **XIP `.data` must be in the image.** An XIP image's `.data` runs in D-RAM but loads
  from I-RAM; the slot image must carry it at its load address, or Zephyr's startup
  copies zeros into `.data` (e.g. an empty-but-not-self-linked `timeout_list`, so
  `k_msleep` never woke up). Zephyr's own `zephyr.bin` (objcopy by LMA) always did;
  the bug was the Makefile overwriting it with an older `scripts/elf2bin.py` that
  dropped such segments. Use `zephyr.bin` as `west build` writes it (byte-identical to
  today's elf2bin output on both boards, checked 2026-09); elf2bin is for the firmware.

## Distribution and releases: `make dist`, tags vouch for a board test

`make dist` (`scripts/make_dist.py`) assembles `build/dist/`, what an application
developer gets (docs/APP_DEVELOPMENT.md is its guide); `make check-dist` copies it away
from the repo and runs the demos with the shipped emulator and Python module, then
`release_check.py selftest`. CI runs both after `make sta` and, on pushes, uploads the tree
as `vux9k-dist-<sha>`. `release.yml` publishes that artifact for an annotated `v*` tag only
if the tag message's `pack.fs sha256` matches it (docs/RELEASING.md): tag the commit whose
CI artifact was tested on the board, never a local build. Keep in sync:
- The dist's contents live in `make_dist.py`'s `FILES`, `check_dist.py`'s `REQUIRED` and
  docs/APP_DEVELOPMENT.md; the docs' Python example is `check_dist.PY_EXAMPLE`, verbatim.
- MANIFEST's tool pins are parsed from `ci.yml`; `make_dist.py` fails if a pattern stops
  matching, so update `PINS` when ci.yml changes shape.
- The distribution is for applications: no Resident Loader image, no firmware hex for the
  emulator (applications start there with `--no-firmware --load IMAGE --mode riscv|hack`,
  the same image and mode as `vux_tool.py flash-sd`), no SD images.

## Firmware rules (Boot Manager, Resident Loader, `fw_common`)

- **Addresses come from `fw_common::map`** (MMIO, mailbox, RL entry, mtime rate);
  logic that needs no MMIO goes into `fw_common` behind a trait and gets a host test
  (`upload` is the `w` exchange over `Host`/`Disk`; a 10/13-sector upload bug hid in
  the Boot Manager until it moved there). The emulator's SD card is lenient about
  SDSC/SDHC addressing unless `strict=True`: use strict for anything card-type related.
- **The VUX9 header exists twice**: `fw_common::header` (the firmware reads it) and
  `SlotHeader`/the constants in `tools/vux_tool.py` (it writes the slots; the dist ships
  it as one file, so nothing is generated). Change both, then regenerate
  `firmware/fw_common/tests/vux9_vectors/` (`VUX9_VECTORS_UPDATE=1 pytest
  sim/emu/test_vux9_header.py`): both parsers are tested against those files, and the
  pytest also compares the constants and field offsets. The Resident Loader reads fields
  by `OFF_*` and, unlike `SlotHeader::parse`, ignores the valid flag (no room).
- **Raise `BOOT_MGR_VERSION` with every Boot Manager change**: boards install a slot-0
  image only if its header version is greater. Tests read it from `main.rs`
  (`sim/emu/bm_env.py`, `scripts/test_hardware.py`); don't hard-code it.
- **The Resident Loader reads a slot twice** (check pass with CRC32, then load pass),
  because lower I-RAM holds the running Boot Manager until the load pass overwrites
  it: errors E1-E5 return to it intact, only a load-pass failure (E6) stops. Keep new
  checks in the first pass. For size, watch what the compiler links in: a
  zero-initialized buffer that is then fully overwritten cost a 152-byte `memset`
  (the header buffer is `MaybeUninit` for that reason).
- **The SD SCLK divider survives a soft reset** (`0x4000_200C`; only power-on resets it).
  The Boot Manager sets `SD_DIV_INIT` before every card init (a strict card reports a
  faster SCLK before it is ready) and `SD_DIV_FAST` after it; the Resident Loader reads at
  whatever the Boot Manager left and never touches the divider.
- **Mailbox requests are marked** (`0xB007_xxxx` launch, `0xA55A_xxxx` update); any
  other value makes the RL restart the Boot Manager. Keep slot in bits 7:0 and SDHC in
  bit 8: older RLs in flashed bitstreams read those.

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
  (so `ma_fetch` skips its RVC part). The Zicntr counters exist: `mcycle`/`minstret`
  (writable) with read-only `cycle`/`instret`, and `time` reads the timer's `mtime`;
  `minstret` counts RV32 instructions that complete without trapping (loads/stores
  in MEM_WAIT, MRET included), and a write to either half of a counter replaces
  that instruction's increment (`rv32mi-p-instret_overflow` checks this; it passed
  vacuously while the counters read 0). CSR writes happen only for CSRRW[I] or a
  non-zero rs1/uimm (`csr_wr`), so plain `csrr` reads have no side effects.
  **Accessing a CSR that isn't implemented is illegal** (as is writing a read-only
  one, `addr[11:10] == 3`, e.g. `unimp`). The legal set is `csr_exists` in
  `rv32i_trap_unit` (README, "RV32 CSRs"); it covers everything Zephyr and the
  riscv-tests env touch, checked by disassembling them. A few are legal only to
  read 0: `mhartid` and the other machine information registers, `mstatush`, and
  the debug trigger registers (`rv32mi-p-breakpoint` writes `tselect` without a
  trap guard), and `mcountinhibit` plus the HPM CSRs the spec requires
  (`mhpmevent3`-`31`, `mhpmcounter3`-`31`(h)), which the ACT4 tests' boot code writes
  unguarded. Before adding a CSR access to firmware, add the CSR to both
  `csr_exists` and `rv32i_csrs`.
- **The tb is split: hardware in Veryl, stimulus in cocotb.** `sim/tb_hex_runner.veryl`
  holds the RAM (preloaded from `program.hex` in the cwd), the CPU and the `tohost`
  latch; `scripts/riscv_tests/hex_runner.py` drives clock/reset, waits for the latch
  with one trigger (not per cycle) and writes `verdict.txt`. `run_riscv_tests.py
  rv32ui-p-add ...` runs a subset; set `TRACE=1` in the environment for a
  per-cycle PC/instruction log in `build/riscv_tests/runs/<test>/sim.log`. Tests run
  in parallel (`--jobs`, default: CPUs); each has its own run directory.
- **The tb's RAM has `soc_ram`'s timing: keep it.** Both ports read one cycle after
  the CPU presents the address, before that edge's write, and stores use `data_waddr`.
  Until 2026-10 it read combinationally at `pc_out`, which hid any fetch-address bug:
  with `pc_out` mutated to `pc_reg` (the address one cycle late) all 56 tests still
  passed; with the synchronous RAM the self-test fails. The combinational read was
  also why the suite took ~97 s on Icarus (each RAM write re-evaluated the reads
  against the whole array); it now takes ~20 s on 2 CPUs. On Icarus a test costs
  ~0.45 s against ~0.7 s on Verilator (process start-up dominates), so `test-isa`
  stays on Icarus, which also keeps unwritten RAM as X.
- **`make test-isa-gls` runs the suite on the gate-level `unified_cpu` netlist**
  (`make synth-units`; `--gls`, results in `runs-gls/`), the tb's memory staying RTL.
  It is the ISA-wide check for constructs Yosys reads differently from the simulators
  (the `as i32` compare above), which the RTL runs and `make eqy` can't see. On
  Verilator, ~70 s with the compile; in `test-sim`.

## `make test-act4`: riscv-arch-test (ACT4) on the same harness

riscv-arch-test 4.1.0's ACT4 tests are self-checking ELFs: the generator runs each test on
the Sail reference model configured like this CPU and builds the expected values in, so a
test can't pass vacuously (the old ACT harness could, see above). `make test-act4`
(`-gls`, `-emu`; all in `test-sim`) runs them through `run_riscv_tests.py --suite act4` on
`tb_hex_runner`, with the riscv-tests' `tohost` (1 = PASS, 3 = FAIL); failure details go
to the testbench's console word (`console.txt` in the run directory). Things to know:
- **The ELFs are generated, not committed.** `make act4-elfs` (`scripts/act4/act4_elfs.py`)
  runs `scripts/act4/generate.sh`, i.e. the upstream image `ghcr.io/riscv/act4:4.1.0`
  (Sail 0.13.1, GCC 16) under docker, podman or podman-remote (`ACT4_ENGINE`), into
  `build/act4/<key>/`, key = a hash of `generate.sh` and `scripts/act4/vux9k/`. A
  configuration change makes new ELFs; CI caches them under the same key. Where no
  container engine exists, `ACT4_ELF_CACHE=<dir>` with `<dir>/<key>/` generated
  elsewhere is copied instead. A run takes ~6-12 min on 2 CPUs.
- **The configuration is three files that must agree:** `vux9k.yaml` (UDB: extensions and
  parameters, what tests are selected and how they check), `sail.json` (the reference
  model: memory map, mtvec/mtval/misaligned behavior, extensions) and
  `rvmodel_macros.h`/`link.ld` (the harness). The CPU starts at 0, the tests at
  `TEST_BASE` 0x4000 (Sail keeps its device tree at 0x1000): `link.ld` puts a jump at 0.
  `sail.json` enables Zihpm although the UDB doesn't list it: with it off, Sail traps on
  `mhpmevent3`-`31`, which the spec requires and the CPU implements (read 0). Sail's CLINT
  must stay enabled (the generator requires it) even though the harness has no timer.
- **Generator errors are configuration errors.** The UDB is validated against 4.1.0's
  schema (parameters that exist in later versions are rejected; some become required
  when others are set, e.g. `MCOUNTENABLE_EN`), and `tests/env/check_defines.h` requires
  every interrupt macro even when the harness can't raise one (they are empty here).
- **`EXPECTED_FAILURES_ACT4`** works like `EXPECTED_FAILURES` (an XPASS fails the run);
  its entries are the places where this harness or CPU legally differs from the Sail
  configuration (no interrupt sources, Sail's CLINT setting MTIP, read-only-zero
  `mcountinhibit`). The self-test (`scripts/act4/selftest_fail.S`, linked like the tests)
  must report FAIL with TESTNUM=1 and its console line before the suite runs.
- `tb_hex_runner` drives the `time` CSR from a cycle counter (as `timer_core` counts); the
  Zicntr tests need it to advance. The emulator's isa-test profile has no console: its
  ACT4 failures carry no details.

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
  in RTL builds, the `board_top` netlist in GLS builds, see "The SoC clock"). Driving the clock
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

## Mutation testing: `make mutation` (mcy, manual only)

`make mutation MCY_TOP=<module>|all [MCY_SIZE=100]` (`scripts/mcy/mutation.py`) asks
whether the unit tests notice a broken RTL module: mcy lists single-point mutations of
the module's generated `.sv` (flattened with its submodules), Yosys `equiv_*` drops the
equivalent ones, and the module's cocotb unit tests (plus the riscv-tests for
`unified_cpu`) run against each remaining mutant, swapped in by `sim_runner`'s
`VUX9K_RTL_OVERRIDE`. Results: `build/mcy/<module>/summary.md` (score and every survivor's
source location), each mutant's test output in `logs/<id>.out`. It is in no tier and has
no threshold: read the survivors, then add a test or note why the mutant is harmless.
Things that went wrong while building it, and must stay fixed:
- **A broken harness looks like a perfect score.** Every mutant "fails" if the tests
  can't run at all. Two such cases: Yosys `write_verilog` renders `$pmux` as functions
  Icarus can't compile (the mutant is now written after `pmuxtree`, and a mutant that
  doesn't compile stops the run), and mcy runs its test scripts under OSS CAD Suite's
  Python wrapper, whose `PYTHONHOME` broke the venv's Python (the script unsets it). The
  run therefore starts with a self-test: the unmutated module through the same script,
  under the same wrapper, must pass (`logs/selftest.out`).
- **The equivalence check needs `memory_map` and `async2sync`**; without them every
  sequential mutant, even mcy's no-op mutation 1, counted as non-equivalent.
- `unified_cpu`'s mutants run one at a time (`run_riscv_tests.py` regenerates shared
  files); expect hours for `MCY_SIZE=100`.

Reading the survivors (2026-10: at size 50, 20 for `unified_cpu`, none is open in any module):
- **Equivalence is decided twice.** `equiv_induct` starts from any state, so a mutant that
  differs only in states the module can't reach (sdcard_spi's `bit_idx` counting
  0,1,6,3,4,5,2,7 is still 8 steps) fails it. For modules with `clk` and `rst` a proof
  from reset follows: the miter with `rst` asserted in step 0, built like sby's AIGER
  model, proven by abc `dprove` (`EQ_RESET`). It took sdcard_spi from 9 survivors to 1.
  Storage without a reset (a FIFO's memory) starts at zero on **each side before the
  miter**: `setundef -zero` on the miter also rewrote `-ignore_gold_x`'s x constants into
  a mask that hid real differences, and a detected mutant came out equivalent. The
  self-test therefore also requires the original to be equivalent to itself and one of
  the first mutants not to be. The proof uses the module's default parameters.
- **A mutated flip-flop output toggles.** `inv`/`cnot` on a `Q` port also feeds the hold
  path (`prep` leaves it as a mux from Q), so the register flips every cycle it holds.
  Whether that shows depends on the hold length's parity: such mutants are often
  equivalent (the proof above) or visible only outside the interface's contract.
- **`ACCEPTED`** in `mutation.py` lists survivors that are equivalent beyond `dprove`'s
  reach (uart_rx's 434-clock timer, the count enumerated instead; a Hack-only register in
  `unified_cpu`) or differ only where the interface promises nothing (uart_rx's `data`
  without `rdy`, fifo_sync's `rdata` while empty), each with its reason; `summary.md` shows them apart from the open
  ones and names entries no survivor matched any more. A survivor that is merely untested
  gets a test, not an entry (the `cov:exclude` rule). `unified_cpu`'s riscv-tests run in a
  256 KB window: what the upper address and PC bits do is the unit test's job
  (`test_unified_cpu.py`).
- `--replay <id>` reruns a module's tests on one mutant of the last run (`--node` picks
  the pytest node), keeping `mutated.v` and the output in `replay/<id>/`; `--summarize`
  rewrites the summaries after editing `ACCEPTED`. mcy's seed is fixed, so ids repeat
  as long as the RTL doesn't change; compare by `summary.md`'s description otherwise.

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
- The riscv-tests are measured too (`run_riscv_tests.py` under `HDL_COVERAGE=1`,
  run directories in `build/riscv_tests/runs-cov/`, hierarchy `tb_hex_runner.uut`).
  GLS is not.

## Firmware coverage: `make coverage-fw` (emulator runs, source lines)

The emulator tests in `sim/emu/` run with `VUX9K_COV_DIR` set, so every SoC made by
`vux9k.start_soc()` records each executed (PC, ISA, instruction word).
`scripts/coverage_fw.py` credits a hit to an image only where that image holds the same
word at that PC (code loaded from SD over the Boot Manager's addresses stays apart).
RV32 hits map to lines through the DWARF of the firmware's `coverage` cargo profile,
whose code the target first `cmp`s against the release image; Hack hits map to the
demo's assembly, checked instruction by instruction against the binary. Output:
`build/coverage/fw/{fw.info,summary.md}`; minimums in `coverage/thresholds.toml`
(runs in `test-sim`). Rules:
- A line that can't run by design gets `cov:exclude(reason)` in a comment on that line
  (e.g. the `nop` after the Resident Loader's soft-reset store). Don't use it for code
  that is merely untested — add the test.
- Raise a threshold when coverage rises; never lower one to get a run through.
- The Hack demo in CI is assembled from the committed `hack_demo/src/main.asm` (`hcc`
  isn't installed there). After changing `main.c`, regenerate it with `hcc -S`.
- Not measured yet: the host crates (emulator core, `fw_common` on its own) — that needs
  cargo-llvm-cov in the CI image.

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

## The emulator must follow the RTL

`emu/` models the SoC cycle for cycle, and `make sim-lockstep` (in `test-sim`) proves it
against the RTL: tb_soc_top.sv writes a trace in RTL builds (`VUX9K_RTL_TRACE`) and
`sim/emu/test_lockstep.py` compares every instruction and UART byte. So:
- **An RTL change that alters behavior needs the matching emulator change in the same
  piece of work**, and a lockstep program that exercises it if none does yet
  (`sim/integration/lockstep_programs.py`). A red lockstep is a finding, not noise.
- The extended profile (and the `vux9k/vux9k/ext` Zephyr board) is emulator-only.
  Nothing that only runs there may be presented as running on the board.
- The SD card exists twice (Python for cocotb, Rust for the emulator); changes go to
  both, and `sim/sd_transcripts/` (regenerated with `SD_TRANSCRIPTS_UPDATE=1 pytest
  sim/emu/test_sd_transcripts.py`) must stay green on both sides.
- Firmware bugs the tests expose are pinned as `xfail(strict=True)` with the reason
  until they are fixed, so the fix shows up as an XPASS.

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

`make test-sim` (every push/PR in CI) runs the unit suites (RTL + GLS), `test-isa`
(+ `test-isa-gls`), and the SoC tests: `sim-soc-fast` (+ `-icarus`), `sim-soc-mmio`,
`sim-boot` (CLI), `sim-hack-rtl`, `sim-sd-quirks`, `sim-hw-flow` (RTL flashing flow)
and `sim-soc-gls-fast`. `make test-slow` (CI nightly + manual `workflow_dispatch`) adds
`sim-gls-hw-flow`, `sim-hw-flow-icarus`, `sim-lockstep-slow`, `sim-zephyr-demo-gls` and
`sim-unit-random` (the unit tests with the date as random seed). Until 2026-09 the flashing flow ran in
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
make sim-unit         # broadest RTL path coverage (26 unit test modules)
make test-isa
make build-hack        # hack_demo/
make sim-hack-rtl
make sim-hw-flow         # full UART/SD chain; longest real interaction (Verilator, ~16 s)
make synth-top          # yosys must resolve build/veryl/soc/{cpu,uart}/*.sv
make build-zephyr-demo   # Zephyr Rust demo for the real board (needs Zephyr)
python3 scripts/check_no_absolute_paths.py
git status                # confirm no stray untracked build output
```
`make sta` and `make test-hw` require real hardware/long PnR runs — skip unless
specifically investigating timing or hardware behavior.
