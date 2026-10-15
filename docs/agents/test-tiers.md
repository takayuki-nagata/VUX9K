---
paths:
  - "Makefile"
  - ".github/workflows/ci.yml"
  - "sim/integration/**"
  - "tools/vux_tool.py"
  - "scripts/test_hardware.py"
  - "firmware/stage1/**"
  - "firmware/stage2/**"
---

# Test tiers

What `test-sim` and `test-slow` cover, and which long tests to run by hand for which change. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Test tiers: what `test-sim` does and doesn't cover

`make test-sim` (every push/PR in CI) runs the firmware's host tests (`test-fw-host`, and
`test-fat`: the FAT32 images rebuilt with dosfstools/mtools, which CI installs), the unit suites (RTL + GLS), `test-isa`
(+ `test-isa-gls`, `test-isa-waits`), and the SoC tests: `sim-soc-fast` (+ `-icarus`), `sim-soc-mmio`,
`sim-boot` (the first stage's prompt), `sim-hack-rtl`, `sim-sd-quirks`, `sim-hw-flow` (RTL RAM
boot and install flow), `sim-stage2` (below), `sim-zephyr-demo-rtl`, `sim-zephyr-irq-echo-rtl` and `sim-soc-gls-fast`. `make test-slow` (CI nightly + manual `workflow_dispatch`) adds
`sim-gls-hw-flow`, `sim-hw-flow-icarus`, `sim-lockstep-slow` (with `s2_run_hello`, the real
second stage booted from the card running the hello demo: ~20M cycles, ~5M instructions traced),
`sim-zephyr-demo-gls` (below), `sim-hw-suite-emu` (the board suite against the emulator,
below), `sim-psram-icarus` (below) and
`sim-unit-random` (the unit tests with the date as random seed). Until 2026-09 the flashing flow ran in
no tier at all (25–35 min RTL / many hours GLS on Icarus); on Verilator it's ~16 s
RTL / ~3–6 min GLS, which is what made it affordable per PR.

`sim-psram-icarus` runs the real PSRAM controller on the IP's pin-level W955D8MBYA model where
`test-sim` doesn't: `test_soc_rv32i` on Icarus RTL (the window checks 18–22 through the real
controller, then `soc_env.check_psram_pins`: the W955 model counted no protocol/timing error;
~30 s) and the PSRAM contract test on the controller's netlist from `make synth-units`
(`test_sim.py::test_psram_contract_gls`). `test-sim` has the controller only in
`sim-psram-contract` (RTL) and in `sim-soc-fast-icarus`, which has no window checks. The
gate-level side is the controller alone because a full-SoC Icarus GLS run with the W955 model
(`test_soc_gls_fast` on the whole netlist) did not finish in 30 min; Verilator GLS has the W955
model on the pins and runs the window through the netlist (`sim-soc-gls-fast`, `sim-gls-hw-flow`),
without its error check (sim.md, "The PSRAM word model and its contract test"). Run it after a new
controller tag or a change to the window's bridge.

`test_soc_hardware_flow.py` is the only RTL test that drives the two-stage boot's whole
UART and SD chain: transfer v2 carries the second stage (35 KB, ~54M cycles at 115200
baud) to the first stage's `u` for a RAM boot, the second stage installs a new
`\VUX9K\STAGE2.BIN` on the FAT32 card (`install`), and after its `reset` the first stage
boots that file. The first stage's other commands are `test_soc_boot.py`'s. RTL reads the card
at ~250 cycles a byte as the board does: the other SoC tests boot a 28-byte `STAGE2.BIN`
(`soc_env.v4_file`), not the second stage.

`test_soc_stage2.py` (`sim-stage2`) is the RTL test of the real second stage booted from the
card (`soc_env.s2_card`: `STAGE2.BIN` and the demos of `make apps` in `\VUX9K\APPS\`): `ls`,
`run HELLO.BIN` (the PSRAM, a0-a2, `r` back to the first stage; ~21M cycles) and `run
HACK.BIN` (I-RAM in Hack mode; ~30M cycles). The prompt alone takes ~14.4M cycles (the 35 KB
file at ~250 cycles a byte), and `run` loads at ~2,000-2,400 cycles a byte (the second stage
runs from the PSRAM): the 13 KB Zephyr demo through it is ~47M cycles, minutes on the
netlist, so it runs there only in `sim-zephyr-demo-gls` (`test_soc_zephyr_s2.py`, test-slow);
the RTL runs the Zephyr demos preloaded into the PSRAM (`soc_env.preload_psram_app`: the word
model's words and a jump at I-RAM 0, so Verilator RTL only). Cycle counts are the emulator's, which the
lockstep holds to the RTL's; measure a new test there first (`s1_env.run_from_card` in `sim/emu/s1_env.py`).

The SD and RAM-boot RTL tests (`sim-boot`, `sim-hw-flow`, `sim-sd-quirks`, the lockstep's
`s1_*`) wait at least the first stage's key window (100 ms, 1.8M cycles) when they let it
boot; Icarus (`sim-soc-fast-icarus`, `sim-hw-flow-icarus`) runs ~19k cycles/s.

Also run `sim-gls-hw-flow` (not in `test-sim`) explicitly whenever you touch:
- transfer v2 (`fw_common::upload`, `vux_tool.send_upload_v2`), the first stage's RAM
  boot (`fw_common::boot::RamBoot`) or the second stage's install,
- the SD boot chain (`firmware/stage1`, `fw_common::fat`, `fw_drivers::sdcard`), or
- anything under `sim/integration/` that talks to the virtual UART/SD models
  (`sdcard_model.py`, `virtual_serial.py`) — a change there can silently
  desync `test_soc_hardware_flow.py`'s own protocol assumptions from the
  firmware's actual behavior (this happened during the 2026-09 `sim/` reorg with the
  first stage's predecessor, undetected until this test was actually run).

It's also worth running once as a final check after any `sim/` directory
layout change (as opposed to content change), even though the fast tests all
pass, precisely because it is the one test exercising the longest real
UART/SD interaction chain and is therefore most likely to surface a subtle
path- or timing-related regression the fast tests can't reach.

## The board tier: what runs on the Tang Nano 9K

None of these run in CI; they need the board. The host setup they check is in
clock-and-board.md, "Board UART output that is noise: ModemManager and the bridge's shared baud rate".
- `make hw-smoke` (after `make timing`): `hw_test` on every routed seed's placement. After
  RTL changes, before flashing.
- `make test-hw`: the two-stage boot suite (`scripts/test_hardware.py`, 15 tests) on a local
  build; `make test-hw-dist DIST=<dir>` the same on a `make dist` tree, the first stage's
  version and the demos (`sd/VUX9K/APPS/`) from it. The card stays in the board and must be
  FAT32; `install` makes `\VUX9K\`, and the suite writes only there (versions 9001-9003,
  `\VUX9K\TEST\` and removes it, the demos in `\VUX9K\APPS\`, last the second stage under
  test). Besides the boot chain it runs the second stage's file commands and the demos
  through `put`/`run`: `HELLO.BIN` (PSRAM), then, last because they never return to the
  first stage, `HACK.BIN` (it replaces the first stage in I-RAM), `ZEPHYR.BIN` and `BC.BIN`
  (in the PSRAM; they leave the first stage, but only the reset button would start it; bc's
  put, load and start take minutes and print their wall-clock times), each followed
  by a load of `pack.fs` into SRAM with openFPGALoader (never `--reset`: that loads the
  flash's bitstream); then it installs the second stage under test again. Without
  `ZEPHYR.BIN`/`BC.BIN` (no Zephyr) tests 13 and 14 are skipped. It runs against the
  emulator too, where the three bitstream-load tests are skipped: `vux9k-emu --tcp
  PORT --sd <mkfat image>` and `--port socket://127.0.0.1:PORT` (~3 min at the board's speed;
  `make sim-hw-suite-emu`, in test-slow, runs it: its timeouts are wall-clock, so a loaded
  host could fail it in `test-sim`; run it by hand after changing the second stage's commands
  or the suite; at `--speed 0` test 7 misses the first stage's key window).
- `make test-hw-irq-echo` (`scripts/hw_irq_echo.py`): the interrupt-driven UART echo put on
  the card and started by the running second stage, then the bitstream loaded again
  (`build/synth/pack.fs`). After `test-hw-dist`, run `make test-hw-irq-echo-dist DIST=<dir>`
  instead: it takes `IRQECHO.BIN` and `pack.fs` from the tree. Given only `--bin`, the script
  once left the board on a weeks-old local `pack.fs` after a dist run. The
  upgrade test from the previous release went with the Boot Manager's slots.
