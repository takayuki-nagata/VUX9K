---
paths:
  - "zephyr_workspace/**"
---

# Zephyr

Read before changing `zephyr_workspace/`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Zephyr: the board `vux9k`

`zephyr_workspace/boards/vux9k/` defines one HWMv2 target on `vux9k-common.dtsi`
(18 MHz, `vux9k,uart`, and the SoC timer as `andestech,machine-timer`, whose
mtime +0x0 / mtimecmp +0x8 layout is `timer_core`'s, so Zephyr's in-tree
`riscv_machine_timer` driver serves it):
- `vux9k`: the real board, linked into the PSRAM: flash = `0x8000_0000`, 4 MB (the image,
  run in place, XIP), sram = `0x8040_0000`, 3 MB (to `0x806F_FFFF`; its top 64 KB is the
  first stage's data, which the first stage sets up again at every start). The nodes are in
  `vux9k.dts` with their unit addresses (`flash@80000000`, `memory@80400000`), not in
  the dtsi. The second stage's `run` loads such an image (a v4 file at `0x8000_0000`, `make
  apps`' `ZEPHYR.BIN`/`IRQECHO.BIN`/`BC.BIN`) in place and jumps to it (fw_common::launch's PSRAM
  target), leaving the first stage in the ROM: a Zephyr app never returns, but Button S1 (the
  board's reset) starts the first stage and the boot again. The tests can't press it, so
  the board tests (`test_hardware.py` 13 and 14, `hw_irq_echo.py`) load the bitstream afterwards.
  A fetch or load there that misses the caches in front of the PSRAM, and every store, waits
  ~35 cycles, so a cycle budget depends on how the code and data fall into the caches.

bc_clone_rs (`make build-zephyr`: `vendor/bc_clone_rs/examples/zephyr_app`, its own
`prj.conf` with a 32 KB main stack; its 64 KB static heap is `HEAP_SIZE` in
`vendor/bc_clone_rs/crates/bc_zephyr/src/lib.rs`; ~260 KB) is built for `vux9k`
like the demos; nothing of it is changed for this board (the submodule is upstream), and
`make apps` makes it `BC.BIN`. It runs from the PSRAM through the caches: the second
stage's `run` loads it in ~52M cycles (2.9 s), and its start to the first `bc> ` is ~51M
(2.8 s; ~74M with the earlier 4 KB instruction cache, 314M, 17.4 s, without the caches). `sim/emu/test_zephyr_bc.py` loads it
straight into the PSRAM for the self-tests and the REPL, starts it once through the second
stage's `run` from a card (`s1_env.run_from_card(..., load_cycles=...)`), and prints the
cycles and `minstret` (`Soc.instret`) to the first `bc> ` and per REPL line: the emulator is
cycle-exact to the RTL (`sim-lockstep`), so these are the RTL's counts (the board's `[TIME]` lines
matched them: 4.1 s to the first `bc> ` with the 4 KB instruction cache, and 17.4 s, 314M cycles at 18 MHz, without the caches),
with the caches' hit rates, to compare speed work against. Its cycle caps are bounds, not measurements. On the board, `test_hardware.py` 14
puts, runs and types at it, printing wall-clock `[TIME]` lines.

`zephyr_workspace/app/` is the Rust demo: Zephyr (`src/main.c`, FFI wrappers for
`printk`/`k_msleep`/`k_uptime_get_32`) calls `rust_main()` from the `rust_demo`
staticlib. `make build-zephyr-demo` builds it for `vux9k` into `build/zephyr-demo/`
(cargo output included). It is tested loaded straight into the PSRAM, on the emulator
(`sim/emu/test_zephyr_demo.py`: `Soc.load_app(image, "riscv", 0x8000_0000)`, the CLI's
default `--load-addr` for riscv) and the RTL (`sim-zephyr-demo-rtl`: `soc_env.preload_psram_app`
writes the PSRAM word model and puts a jump to `0x8000_0000` at ROM word 0, so Verilator RTL
only), and as the board starts it, the second stage's `run VUX9K/APPS/ZEPHYR.BIN` from the
card: on the emulator (the same test, same expectations) and on the netlist
(`sim-zephyr-demo-gls`, test-slow: the prompt at ~12.8M cycles, the 13 KB load ~3.5M, then
the demo). Its `prj.conf` size settings, from the 14 KB I-RAM board it was first
linked for, keep it small and so quick to load.

Two traps found while bringing Zephyr up on the RTL (the old Python emulator hid both):
- **The SoC must select Zifencei.** The Zephyr SDK has no libgcc multilib for
  `rv32i_zicsr`; GCC silently links a default one built with RV32M, and the first
  64-bit division (`__udivdi3` -> `divu`) is an illegal instruction.
- **XIP `.data` must be in the image.** An XIP image's `.data` runs in its RAM but loads
  from the image (both in the PSRAM now; D-RAM and I-RAM on the first board); the image
  must carry it at its load address, or Zephyr's startup
  copies zeros into `.data` (e.g. an empty-but-not-self-linked `timeout_list`, so
  `k_msleep` never woke up). Zephyr's own `zephyr.bin` (objcopy by LMA) always did;
  the bug was the Makefile overwriting it with an older `scripts/elf2bin.py` that
  dropped such segments. Use `zephyr.bin` as `west build` writes it (byte-identical to
  elf2bin's output when checked, 2026-09); elf2bin is for the firmware.

## Zephyr: the interrupt-driven UART

`uart_vux9k.c` implements the interrupt-driven API under `CONFIG_UART_INTERRUPT_DRIVEN`
(the polling build, and so the demo's `zephyr.bin`, is unchanged by it). What the
hardware gives and what the driver makes up for:
- **RX is the machine external interrupt** (dts `interrupts-extended = <&cpu0_intc 11>`):
  `soc_top` wires `ext_irq_in` to `!uart_empty`, level, with no enable bit in the UART.
  `uart_irq_rx_disable()` therefore masks MEIE in `mie`; leaving it enabled with a
  callback that doesn't read the FIFO dry re-enters the ISR forever (with no callback
  set, the ISR masks it itself).
- **There is no TX interrupt.** `uart_irq_tx_enable()` runs the callback at once (under
  `irq_lock` when called from a thread; from inside the callback only a flag is set;
  a thread the callback wakes there runs at the next reschedule point, not at once),
  then a `k_timer` re-runs it every 700 us, rounded up to a tick, while TX stays enabled.
  The board runs 1000 ticks/s (`vux9k_defconfig`) for this: at the earlier 100 it was
  one 32-byte refill per 10 ms (~3 KB/s). An app that lowers
  `CONFIG_SYS_CLOCK_TICKS_PER_SEC` slows its TX the same way. The kernel is tickless, so
  the rate costs no periodic interrupts; the demo tests check `k_msleep(100)` to 102 ms,
  a tick plus rounding, which fails at 100 ticks/s (110 or more). From the PSRAM without
  its caches it read 103-104 ms (the kernel's own time).
- `irq_tx_complete` and error interrupts are left out on purpose: the status register
  has no "transmitter empty" bit, and errors are only sticky flags.
- **Errors aren't lost to the driver's own status reads.** The overrun and frame-error
  bits are sticky, but every read of the status register clears them, and the driver reads
  it all the time (RX empty, TX full, in the ISR and in threads), so `err_check` used to
  find them cleared (irq_echo printed `err=0` for a line that had lost bytes). Every status
  read goes through `uart_vux9k_read_status()`, which ORs the error bits into the driver
  data (under `irq_lock`, only when one is set), and `err_check` returns and clears them.
  This is in the polling build too, so it changed the demo's `zephyr.bin`.
- **RX at full speed needs the instruction cache.** From the uncached PSRAM (before the
  cache) the RX handler took about 4.3 frame times a byte, so input longer than the FIFO
  overran at 115200 baud (a 64-byte line came back with 52 bytes). With the cache the
  back-to-back line comes back whole. The paced line stays: the board test sends it by
  default (16-byte chunks, a chunk every 176 frame times, `sim/integration/irq_echo_pace.py`,
  shared by the emulator, RTL and board tests; the emulator test prints the measurement and
  fails below a 2x margin). The overrun check (`err=1`, `UART_ERROR_OVERRUN`) now sends its
  line from irq_echo's first cycle, while it starts and before it enables RX: the FIFO keeps
  32 bytes, and the reply is exactly those. The emulator test checks that the app became
  ready only after the FIFO had overflowed (its precondition).

A TX interrupt and an interrupt-enable register would only come with a later hardware
revision, whose register map isn't bound by this one's (docs/APP_DEVELOPMENT.md, "Hardware
revisions and compatibility"); this revision doesn't add them.

`zephyr_workspace/irq_echo/` exercises all of it: main sleeps on a semaphore the RX
callback gives, and each line comes back upper-cased in one piece (longer than the TX
FIFO, so the timer path runs) with `rx=<n> drop=<n> err=<n>`. It does without printk
(cbprintf alone is 1.5 KB), so it stays small and loads quickly, like the Rust demo. Its handlers run from the PSRAM like the rest. Tests:
`sim/emu/test_zephyr_irq_echo.py` (loaded into the PSRAM, and through the second stage's
`run VUX9K/APPS/IRQECHO.BIN`) and `sim-zephyr-irq-echo-rtl` (preloaded into the PSRAM); on
the board, `make test-hw-irq-echo` (`scripts/hw_irq_echo.py`: `put` and `run` through the
second stage, lines paced from the host as above, `--chunk 0` back to back, then the
bitstream loaded into SRAM again).
