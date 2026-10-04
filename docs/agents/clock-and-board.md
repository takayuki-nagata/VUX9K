---
paths:
  - "soc/board_top.veryl"
  - "soc/soc_pkg.veryl"
  - "scripts/hw_smoke.py"
  - "scripts/test_hardware.py"
  - "firmware/hw_test/**"
  - "zephyr_workspace/boards/**"
---

# The SoC clock and the board

Read before changing the clock, the PLL, anything timed in ticks, or debugging the board. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

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
