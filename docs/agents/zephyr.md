---
paths:
  - "zephyr_workspace/**"
---

# Zephyr

Read before changing `zephyr_workspace/`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Zephyr: the board `vux9k`

`zephyr_workspace/boards/vux9k/` defines one HWMv2 target on `vux9k-common.dtsi`
(18 MHz, the UART as `litex,uart0` (Zephyr's `uart_liteuart` driver; below), and the CLINT, `timer_core` at `0x2000_0000`, as
`sifive,clint0`, whose mtimecmp +0x4000 / mtime +0xBFF8 Zephyr's in-tree
`riscv_machine_timer` driver uses, its timer interrupt the node's second
`interrupts-extended` entry (MSI 3, then MTI 7); before the CLINT the board used the
TIMER page at `0x4000_1000` as `andestech,machine-timer`, which a v0.3.x image still
expects and the hardware no longer has: that page is unassigned now):
- `vux9k`: the real board, linked into the PSRAM: flash = `0x8000_0000`, 4 MB (the image,
  run in place, XIP), sram = `0x8040_0000`, 3 MB (to `0x806F_FFFF`; its top 64 KB is the
  first stage's data, which the first stage sets up again at every start). The nodes are in
  `vux9k.dts` with their unit addresses (`flash@80000000`, `memory@80400000`), not in
  the dtsi. The second stage's `run` loads such an image (a v4 file at `0x8000_0000`, `make
  apps`' `ZEPHYR.BIN`/`IRQECHO.BIN`/`BC.BIN`/`MPY.BIN`) in place and jumps to it (fw_common::launch's PSRAM
  target), leaving the first stage in the ROM: a Zephyr app never returns, but Button S1 (the
  board's reset) starts the first stage and the boot again. The tests can't press it, so
  the board tests (`test_hardware.py` 13 to 15, `hw_irq_echo.py`) load the bitstream afterwards.
  A fetch there that misses the instruction cache in front of the PSRAM (8-word lines) waits ~49 cycles, a
  load or store that misses the data cache (8-word lines, write-back) ~49 (~99 with a
  dirty line written back first), so a cycle budget depends on how the code and data fall
  into the caches.

bc_clone_rs (`make build-zephyr`: `vendor/bc_clone_rs/examples/zephyr_app`, its own
`prj.conf` with a 32 KB main stack; its 64 KB static heap is `HEAP_SIZE` in
`vendor/bc_clone_rs/crates/bc_zephyr/src/lib.rs`; ~260 KB) is built for `vux9k`
like the demos; nothing of it is changed for this board (the submodule is upstream), and
`make apps` makes it `BC.BIN`. It runs from the PSRAM through the caches: the second
stage's `run` loads it in ~39M cycles (2.2 s), and its start to the first `bc> ` is ~14.5M
(0.8 s; ~36M with the earlier write-through data cache of one-word lines, ~51M before RV32M, ~74M with the earlier 4 KB instruction cache, 314M, 17.4 s, without the caches). `sim/emu/test_zephyr_bc.py` loads it
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
(`sim-zephyr-demo-gls`, test-slow: the prompt at ~11.0M cycles, the 13 KB load ~2.5M, then
the demo). Its `prj.conf` size settings, from the 14 KB I-RAM board it was first
linked for, keep it small and so quick to load.

Two traps found while bringing Zephyr up on the RTL (the old Python emulator hid both):
- **The SoC must select Zifencei.** GCC links the libgcc multilib of the exact `-march`;
  the Zephyr SDK had none for `rv32i_zicsr`, so GCC silently linked a default one built with
  RV32M, and on the CPU before RV32M the first 64-bit division (`__udivdi3` -> `divu`) was
  an illegal instruction. The SoC is `rv32im_zicsr_zifencei` now (`RISCV_ISA_EXT_M`, the
  dtsi's `riscv,isa`); keep the march one the SDK has a multilib for.
- **XIP `.data` must be in the image.** An XIP image's `.data` runs in its RAM but loads
  from the image (both in the PSRAM now; D-RAM and I-RAM on the first board); the image
  must carry it at its load address, or Zephyr's startup
  copies zeros into `.data` (e.g. an empty-but-not-self-linked `timeout_list`, so
  `k_msleep` never woke up). Zephyr's own `zephyr.bin` (objcopy by LMA) always did;
  the bug was the Makefile overwriting it with an older `scripts/elf2bin.py` that
  dropped such segments. Use `zephyr.bin` as `west build` writes it (byte-identical to
  elf2bin's output when checked, 2026-09); elf2bin is for the firmware.

## Zephyr: MicroPython

MicroPython's Zephyr port is built for `vux9k` from its release tarball, never from code in
this repository: `make micropython-src` downloads `micropython-<MICROPY_VERSION>.tar.xz`
(the Makefile pins the version and its sha256; ~160 MB with `lib/`'s submodules), checks it
and unpacks it into `vendor/micropython-<version>/` (gitignored, like `vendor/riscv-tests`;
CI caches it under the sha256). To move to a new release, change both and read the port's
`prj.conf`, `mpconfigport.h` and `README.md` (which Zephyr releases it supports) again. Things that
aren't obvious:
- **The configuration is ours**: `zephyr_workspace/micropython/prj.conf` replaces the port's
  (`-DCONF_FILE`), based on its full `prj.conf` less networking, watchdog, FPU (an rv32im SoC
  can't select it) and reboot, plus the extra features (f-strings, `re`; bc_clone_py needs
  them), a 256 KB heap and a 16 KB main stack (bc_clone_py's parser and evaluator recurse on
  it). Not `prj_minimal.conf`: its
  `mpconfigport_minimal.h` has no `time` module and `long long` integers.
- **The configuration header is ours too**: `mpconfigport_vux9k.h` includes the port's
  `mpconfigport.h` and turns the `zsensor` module off, which names sensor attributes Zephyr
  v3.7 doesn't have (the port's CI builds against v4.4, although its README lists v3.7).
  `build-micropython` passes it as `CONFIG_MICROPY_CONFIGFILE` on the command line (an
  absolute path), and builds from scratch when a file in `zephyr_workspace/micropython/` is
  newer than the build's `.config`: MicroPython's module scan re-reads only changed sources,
  so a kept build still registered `zsensor` (an undefined reference at the link). After
  changing the Makefile's flags for it, remove `build/micropython/` by hand.
- **Console: the UART console's input hook, not the console subsystem.** With
  `prj_minimal.conf`'s `CONSOLE_SUBSYS` the port reads through a tty, which sees Ctrl-C only
  when the REPL asks for a character: `while True: pass` could not be stopped. Without it the
  port's `src/zephyr_getchar.c` takes each byte in the RX interrupt (Ctrl-C raises
  `KeyboardInterrupt` at once) and output is `uart_poll_out`, so the TX interrupt isn't used.
- **The banner's version is `MICROPY_GIT_TAG`** (`build-micropython` exports it):
  otherwise `py/makeversionhdr.py` runs `git describe` in the unpacked tree, finds this
  repository around it, and the image says `MicroPython <our commit>-dirty`.
- **bc_clone_py is frozen in**: `make micropython-src` also fetches a pinned commit of it
  (`BC_PY_COMMIT`, its GitHub archive and sha256: GitHub's generated archives have changed
  bytes before, so a sha256 failure on an unchanged pin means re-taking the sum; CI caches the
  tree) into `vendor/bc_clone_py`, and
  `zephyr_workspace/micropython/manifest.py` (`CONFIG_MICROPY_FROZEN_MANIFEST`, on the command
  line like the header) freezes its five modules. It is upstream: a change goes to its
  repository (its `micropython` branch made it run here: `input()` for its REPL, since
  `sys.stdin.readline()` on this console neither echoes nor ends a line at a terminal's
  `\r`; no `sys.stdout.flush()`, which this port's stdout lacks) and comes back as a new pin.
  mpy-cross, which compiles the frozen modules, is built into `build/mpy-cross/`
  (`MICROPY_MPYCROSS`, one directory per version); MicroPython's CMake would build it inside
  the vendor tree. A new pin rebuilds MPY.BIN from scratch: the archive keeps its files' commit
  times, and the frozen modules are recompiled only when newer than the last build's.
- The build writes only into `build/` (checked 2026-10-20: nothing in the vendor tree
  changes), compiles no `lib/` component (`make_dist.py`'s license note says so;
  check again when the configuration grows), and has no F instructions (RV32M since the SoC selects it).

`make apps` makes it `MPY.BIN` (~351 KB). `sim/emu/test_zephyr_micropython.py` loads it
into the PSRAM and types at the REPL (an expression, `2**100`, a function, an exception,
`time.sleep_ms(100)` against `ticks_ms`, Ctrl-C stopping a loop), runs bc_clone_py's
self-test (each case timed; ~197M cycles in all, 5-29M a case (~373M with the earlier caches, ~470M before RV32M), its heap peaking at ~36 KB)
and types at its REPL as a terminal does (`\r`; no error may show), starts it once through
the second stage's `run` from a card, and prints cycles like bc's test: ~0.32M cycles (20 ms)
from its start to the first `>>> `, 0.2-0.4M a short line, 4-9M (0.2-0.5 s) a bc line, and ~65M
from power-on through both stages and `run`'s load to its start. The interpreter runs at ~5 cycles an instruction
from the PSRAM, so a few thousand bytecodes take milliseconds: the sleep test takes the
interpreter's own time (the same line with `sleep_ms(0)`) off before it checks the 100 ms. On the board,
`test_hardware.py` 15 puts and runs it, types three lines, runs bc_clone_py's self-test and a
line at its REPL, printing `[TIME]` lines.

## Zephyr: the interrupt-driven UART

The UART has LiteX liteuart's registers (`soc/uart/uart_regs.veryl`), so the board uses
Zephyr's own `uart_liteuart.c` (`litex,uart0`, `CONFIG_UART_LITEUART`); this module has no
driver of its own (the former `uart_vux9k.c`, with its emulated TX interrupt, is gone).
What the board adds for it and what to know:
- **The interrupt's two cells.** The driver connects `DT_INST_IRQN(0)` with
  `DT_INST_IRQ(0, priority)`, and `riscv,cpu-intc` has one cell (the irq), so a UART node
  wired to it doesn't compile. `vux9k-common.dtsi` puts a node in between,
  `vux9k,meip-intc` (`dts/bindings/interrupt-controller/`, two cells, no driver), wired to
  `cpu0_intc` 11, and the UART names `interrupts = <11 0>` on it. With
  `CONFIG_MULTI_LEVEL_INTERRUPTS` off, `DT_IRQN` is the irq cell itself, 11: the ISR is
  mcause 11's, MEI. `soc.h` gives the driver its `litex_read8`/`litex_write8` (`sys_read8`/
  `sys_write8` at the register's word address: `lbu`/`sb`, which the UART takes as word
  accesses).
- **The events are levels** (EV_TX: the TX FIFO isn't full; EV_RX: the RX FIFO isn't
  empty), not LiteX's latched ones; the driver's write of EV_RX to EV_PENDING after each
  byte does nothing, and nothing else needs it. EV_PENDING is EV_STATUS masked by EV_ENABLE
  once EV_ENABLE isn't 0 (with EV_ENABLE 0 it is EV_STATUS: Linux's liteuart, polled, never
  enables anything and services RX/TX only from EV_PENDING): the driver's `irq_is_pending`
  reads it, and `uart_console.c`'s ISR loops `while (uart_irq_update() &&
  uart_irq_is_pending())`, skipping the RX work when no byte is there. With EV_PENDING
  unmasked while RX alone was enabled, its TX bit (room in the FIFO) kept that loop going
  forever: MicroPython's REPL hung in the ISR after its prompt. With EV_TX enabled and room in the FIFO the
  interrupt is pending all the time, so a callback disables TX (`uart_irq_tx_disable()`)
  once it has nothing to send; irq_echo's does. TX is a real interrupt now: the 700 us
  k_timer of the old driver, and the reason the board runs 1000 ticks/s, are gone (the
  ticks stay at 1000: the demo tests check `k_msleep(100)` to 102 ms).
- **EV_ENABLE resets with the soft reset**, the FIFOs don't: an application that left an
  event enabled would otherwise interrupt the next one as soon as its driver's init calls
  `irq_enable` (the stages enable no interrupts).
- `uart_err_check()` returns `-ENOSYS`: the UART reports no receive errors (a byte that
  arrives with the RX FIFO full is dropped, a frame with a low stop bit is received), and
  the driver has no `err_check`. A lost byte shows in irq_echo's `rx=` count. (Up to v0.3.x
  the status had sticky overrun/frame-error bits that a read cleared.)
- **RX at full speed needs the instruction cache.** From the uncached PSRAM (before the
  cache) the RX handler took about 4.3 frame times a byte, so input longer than the FIFO
  overran at 115200 baud (a 64-byte line came back with 52 bytes). With the cache the
  back-to-back line comes back whole. The paced line stays: the board test sends it by
  default (16-byte chunks, a chunk every 176 frame times, `sim/integration/irq_echo_pace.py`,
  shared by the emulator, RTL and board tests; the emulator test prints the measurement and
  fails below a 2x margin; the TX interrupts the echo now takes while a line comes in are
  part of what it measures). The overrun check (`rx=32 drop=0`) sends a line that
  arrives before irq_echo enables RX: the FIFO keeps 32 bytes, and the reply is exactly those.
  The line is 33 bytes, the shortest that overflows the FIFO. It no longer races irq_echo's
  start-up, which the instruction cache's lines moved ahead of it (RX enabled after ~32k
  cycles, the line taking 51.5k on the emulator and 56.6k from the RTL's bridge, two stop bits:
  sending with one stop bit wouldn't have closed that gap): `irq_echo_pace.hold_words` is a
  ROM stub in place of the plain jump that waits `HOLD_CYCLES` (the slowest line and 4 frames'
  margin, 63.5k) on mtime before it jumps to the app, leaving the UART alone, so the full
  FIFO is the app's to find. Both tests check the precondition two frames before the hold
  ends (the line has left the host and the RX FIFO is full: the RTL `uart_inst.rx_full`, the
  emulator the RX level and its count of dropped bytes; and nothing yet sent), so a lost race
  says so instead of reading as a wrong echo.

`zephyr_workspace/irq_echo/` exercises all of it: main sleeps on a semaphore the RX
callback gives, and each line comes back upper-cased in one piece (longer than the TX
FIFO, so the TX interrupt refills it) with `rx=<n> drop=<n>`. It does without printk
(cbprintf alone is 1.5 KB), so it stays small and loads quickly, like the Rust demo. Its handlers run from the PSRAM like the rest. Tests:
`sim/emu/test_zephyr_irq_echo.py` (loaded into the PSRAM, and through the second stage's
`run VUX9K/APPS/IRQECHO.BIN`) and `sim-zephyr-irq-echo-rtl` (preloaded into the PSRAM); on
the board, `make test-hw-irq-echo` (`scripts/hw_irq_echo.py`: `put` and `run` through the
second stage, lines paced from the host as above, `--chunk 0` back to back, then the
bitstream loaded into SRAM again).
