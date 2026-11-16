---
paths:
  - "Makefile"
  - ".github/workflows/ci.yml"
  - "sim/integration/**"
  - "tools/vux_tool.py"
  - "scripts/test_hardware.py"
  - "scripts/test_hw_upgrade.py"
  - "firmware/stage1/**"
  - "firmware/stage2/**"
  - "linux_workspace/**"
---

# Test tiers

What `test-sim` and `test-slow` cover, and which long tests to run by hand for which change. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Which tests a change needs: by what it touches

Pick the runs from the paths a branch changes, not from habit: a tier that cannot see the
change only costs hours of the one CI machine, and the test that can see it may be in no tier.
`make check` runs for every change (the pre-commit hook). Then:

| The branch touches | Run | Not needed for it alone |
|---|---|---|
| `linux_workspace/` only (the kernel's fragment, patches and device tree, buildroot's tree, `userland/`) | `make linux-dist test-linux-emu` on each change that builds; at the end, the board's `make test-hw-linux-dist` with that `linux-dist` (CI's `linux` job does the same two targets) | `test-sim`, `test-slow`, `dist`: none of them builds or boots Linux, and `pack.fs` doesn't change |
| The Linux test scripts (`scripts/hw_linux.py`, `scripts/make_linux_dist.py`, `sim/integration/linux_checks.py`, `sim/emu/test_linux.py`) | the above, and their unit tests: `pytest sim/emu/test_hw_dist.py sim/emu/test_make_linux_dist.py` | `test-sim`'s RTL and GLS runs |
| `emu/`, the card models (`sim/integration/sdcard_protocol.py`), `sim/sd_transcripts/`, `firmware/`, `tools/`, other `scripts/` and `sim/` | `make test-sim` (plus `test-linux-emu` when Linux boots on what changed: the emulator's devices, the second stage's `linux`) | `test-slow`, unless a row of sync-points.md asks for one of its targets |
| `soc/` (RTL), the cocotb harness (`sim/runners/`, `soc_env.py`, `tb_soc_top.sv`), `Veryl.toml`, a submodule | `make test-sim`, then at the end of the branch `make test-slow` (GLS, Icarus, the long lockstep), `make timing` and the board's `hw-smoke` | — |
| `docs/`, the agent guides | `make check` (it checks the guides' paths and cited sections) | everything else |

The board tier follows the same rule: `test-hw-dist` when the stages, the bitstream or the
card's files change, `test-hw-linux-dist` when the kernel or its userland does. A stage that
changes nothing below `linux_workspace/` is verified by the first row alone, at its end on the
board. Record what was run and why in the branch's notes, including what was left out.

## Test tiers: what `test-sim` does and doesn't cover

`make test-sim` (every push/PR in CI) runs the firmware's host tests (`test-fw-host`, and
`test-fat`: the FAT32 images rebuilt with dosfstools/mtools, which CI installs), the emulator's
tests (`emu-test`, `test-emu`, `test-isa-emu`, `test-act4-emu`), the coverage checks
(`coverage-fw`, `coverage-fcov`, `coverage-rust`), `sim-lockstep`, the unit suites (RTL + GLS)
and `sim-psram-contract`, `test-isa` and `test-act4` (+ `test-isa-gls`, `test-act4-gls`,
`test-isa-waits`), and the SoC tests: `sim-soc-fast` (+ `-icarus`), `sim-soc-mmio`,
`sim-boot` (the first stage's prompt), `sim-hack-rtl`, `sim-sd-quirks`, `sim-hw-flow` (RTL RAM
boot and install flow), `sim-stage2` (below), `sim-zephyr-demo-rtl`, `sim-zephyr-irq-echo-rtl` and `sim-soc-gls-fast`. `make test-slow` (CI nightly + manual `workflow_dispatch`) adds
`sim-gls-hw-flow`, `sim-hw-flow-icarus`, `sim-lockstep-slow` (with `s2_run_hello`, the real
second stage booted from the card running the hello demo: ~20M cycles, ~5M instructions traced),
`sim-zephyr-demo-gls` (below), `sim-hw-suite-emu` (the board suite against the emulator,
below), `sim-psram-icarus` (below) and
`sim-unit-random` (the unit tests with the date as random seed). Until 2026-09 the flashing flow ran in
no tier at all (25–35 min RTL / many hours GLS on Icarus); on Verilator it was ~16 s RTL /
~3–6 min GLS, which made it affordable per PR. Since it RAM-boots the second stage
(35 KB then, ~54M cycles; 38 KB since v11) it takes far longer: on a 2-vCPU machine (2026-10-18) ~11 min RTL, ~3 h GLS
(test-slow) and ~4.5 h on Icarus (test-slow).

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
UART and SD chain: transfer v2 carries the second stage (38 KB, ~59M cycles at 115200
baud) to the first stage's `u` for a RAM boot, the second stage installs a new
`\VUX9K\STAGE2.BIN` on the FAT32 card (`install`), and after its `reset` the first stage
boots that file. The first stage's other commands are `test_soc_boot.py`'s. RTL reads the card
at ~230 cycles a byte as the board does: the other SoC tests boot a tiny `STAGE2.BIN`
that prints '#' (`soc_env.v4_file`; `soc_env.v4_size` gives the size the first stage shows
for it), not the second stage.

`test_soc_stage2.py` (`sim-stage2`) is the RTL test of the real second stage booted from the
card (`soc_env.s2_card`: `STAGE2.BIN` and the demos of `make apps` in `\VUX9K\APPS\`): `ls`,
`run HELLO.BIN` (the PSRAM, a0-a2, `r` back to the first stage; ~12.4M cycles) and `run
HACK.BIN` (its PSRAM segment in Hack mode, then its soft reset back to the first stage;
~15.5M cycles). The prompt alone takes ~11.0M cycles (the 38 KB
file at ~230 cycles a byte), and `run` loads at ~100 cycles a byte (the second stage
runs from the PSRAM, through its caches, and reads runs of clusters straight into place): the 13 KB Zephyr demo through it is ~16.4M cycles,
minutes on the netlist, so it runs there only in `sim-zephyr-demo-gls` (`test_soc_zephyr_s2.py`, test-slow);
the RTL runs the Zephyr demos preloaded into the PSRAM (`soc_env.preload_psram_app`: the word
model's words and a jump at ROM word 0, so Verilator RTL only). Cycle counts are the emulator's, which the
lockstep holds to the RTL's; measure a new test there first (`s1_env.run_from_card` in `sim/emu/s1_env.py`).

The SD and RAM-boot RTL tests (`sim-boot`, `sim-hw-flow`, `sim-sd-quirks`, the lockstep's
`s1_*`) wait at least the first stage's key window (100 ms, 1.8M cycles) when they let it
boot; Icarus (`sim-soc-fast-icarus`, `sim-hw-flow-icarus`) ran ~19k cycles/s in 2026-09;
`sim-hw-flow-icarus` took ~4.5 h on the 2-vCPU machine above.

Also run `sim-gls-hw-flow` (not in `test-sim`) explicitly whenever you touch:
- transfer v2 (`fw_common::upload`, `vux_tool.send_upload_v2`), the first stage's RAM
  boot (`fw_common::boot::RamBoot`) or the second stage's install,
- the SD boot chain (`firmware/stage1`, `fw_common::fat`, `fw_drivers::sdcard`), or
- the harness that drives the virtual UART/SD models (`sdcard_model.py`'s pin-level
  wrapper, `virtual_serial.py`) — a change there can silently desync
  `test_soc_hardware_flow.py`'s own protocol assumptions from the firmware's actual behavior
  (this happened during the 2026-09 `sim/` reorg with the first stage's predecessor,
  undetected until this test was actually run). A change to the card's answers alone
  (`sdcard_protocol.py`, held to the emulator's card by `sim/sd_transcripts/`) is what the
  RTL `sim-hw-flow` in `test-sim` already runs with the same model; the netlist adds nothing
  to it while the RTL is unchanged.

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
- `make test-hw`: the two-stage boot suite (`scripts/test_hardware.py`, 16 tests) on a local
  build; `make test-hw-dist DIST=<dir>` the same on a `make dist` tree, the first stage's
  version and the demos (`sd/VUX9K/APPS/`) from it. The card stays in the board and must be
  FAT32; `install` makes `\VUX9K\`, and the suite writes only there (versions 9001-9003,
  `\VUX9K\TEST\` and removes it, the demos in `\VUX9K\APPS\`, last the second stage under
  test). Besides the boot chain it runs the second stage's file commands and the demos
  through `put`/`run`: `HELLO.BIN` (PSRAM) and `HACK.BIN` (its PSRAM segment), which both
  soft-reset back to the first stage and its autoboot of the second stage, then, last,
  `ZEPHYR.BIN`, `BC.BIN` and `MPY.BIN`
  (in the PSRAM; they leave the first stage, but only the reset button would start it; bc's
  put, load and start take minutes and print their wall-clock times, as MicroPython's do), each followed
  by a load of `pack.fs` into SRAM with openFPGALoader (never `--reset`: that loads the
  flash's bitstream); then it installs the second stage under test again. Without
  `ZEPHYR.BIN`/`BC.BIN`/`MPY.BIN` (no Zephyr) tests 13 to 15 are skipped. It runs against the
  emulator too, where the three bitstream-load tests are skipped: `vux9k-emu --tcp
  PORT --sd <mkfat image>` and `--port socket://127.0.0.1:PORT` (~3 min at the board's speed;
  `make sim-hw-suite-emu`, in test-slow, runs it: its timeouts are wall-clock, so a loaded
  host could fail it in `test-sim`; run it by hand after changing the second stage's commands
  or the suite; at `--speed 0` test 7 misses the first stage's key window).
- `make test-hw-irq-echo` (`scripts/hw_irq_echo.py`): the interrupt-driven UART echo put on
  the card and started by the running second stage, then the bitstream loaded again
  (`build/synth/pack.fs`). After `test-hw-dist`, run `make test-hw-irq-echo-dist DIST=<dir>`
  instead: it takes `IRQECHO.BIN` and `pack.fs` from the tree. Given only `--bin`, the script
  once left the board on a weeks-old local `pack.fs` after a dist run.
- `make test-hw-linux LINUX=<dir>` (`scripts/hw_linux.py`; `test-hw-linux-dist DIST=<dir>` takes
  that tree's `pack.fs`): the noMMU Linux kernel of a `make linux-dist` tree (CI's
  `vux9k-linux-<sha>`) put on the card in `\VUX9K\LINUX\`, its programs for the card in
  `BIN\`, MicroPython's modules as `PYLIB.TAR` (~6 min for the 3.2 MB Image, ~1 min for the
  0.5 MB `MPY`; `HW_LINUX_FLAGS=--keep-card` boots the files already
  there), then `pack.fs` loaded into SRAM, the first stage booting the second, whose `linux`
  starts the kernel; the boot lines and commands of `sim/integration/linux_checks.py` (the
  emulator's `test_linux.py` checks the same; the kernel mounts the card, runs programs, the
  text tools, MicroPython (its REPL too; linux.md, "MicroPython") and the sample scripts from
  it, writes `\VUX9K\SDTEST.TXT` and reads it back),
  vi driven by `VI_SESSIONS`' keys (`\VUX9K\VITEST.TXT`), then `CARD_CLEANUP` removes the
  files and unmounts the card, and last a paste at the shell, back to back and paced as the
  monitor sends it (linux.md, "Text tools and vi"). `[TIME]` lines from the load to the
  shell's prompt, each command, each of the card's busyboxes' first exec and vi's sessions,
  `[MEM]` lines with the memory table read right after the boot (`linux_checks.MEM_PROBES`;
  linux.md, "Memory budget"), `[PASTE]` lines with the lines that came through, and `pack.fs`
  loaded again at the end.
  `make test-linux-emu` runs the script against the emulator's CLI at full speed twice:
  as it is (`test_board_script_on_the_emulator`) and with the emulator stopped for a moment
  while each command line is typed (`test_board_script_with_emulator_stalls`, as a busy CI
  host deschedules it).
- `make test-hw-upgrade DIST=<dir>` (`scripts/test_hw_upgrade.py`; `make prev-release` fetches
  `V03_RELEASE`, v0.3.0, and `PREV_RELEASE`, v0.4.0, into `build/previous/`), with the tree's
  second stage on the card (as `test-hw-dist` leaves it) and no card writes: v0.3.0's `pack.fs`
  into SRAM, its first stage booting that second stage, which stops at its RV32M check; then
  the previous release's `pack.fs`, its first stage booting the same second stage to `s2> `;
  then the tree's, the same. The output before the port reopens after a load may be cut, so
  step 1 waits only for the second stage's message. The card half is `sim/emu/test_upgrade.py`
  in `test-emu`. From v0.3.0: a card with its second stage and demos given to this tree's first
  stage, which refuses that second stage (older than `MIN_STAGE2_VERSION`) and stays at `s1> `,
  then docs/APP_DEVELOPMENT.md's upgrade steps with this tree's `vux_tool` (`install-stage2`
  from there, `put`, `run-app`, `r` back to the new second stage); and v0.3.0's first stage on
  a CPU without M (`m_ext`) and with its MMIO, the TIMER page and the UART's older registers
  (`v03_mmio`), booting this tree's second stage, which prints its no-M message through that
  UART. From the previous release: its card on this tree's first stage, whose second stage
  boots and runs its HELLO and HACK as they are, then `install-stage2`, the same HELLO and HACK
  under this tree's second stage, and the rest of the install steps; and its first
  stage booting this tree's second stage to `s2> `.
