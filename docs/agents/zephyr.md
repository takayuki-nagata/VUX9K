---
paths:
  - "zephyr_workspace/**"
---

# Zephyr

Read before changing `zephyr_workspace/`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

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
  The boards run 1000 ticks/s (`vux9k*_defconfig`) for this: at the earlier 100 it was
  one 32-byte refill per 10 ms (~3 KB/s). An app that lowers
  `CONFIG_SYS_CLOCK_TICKS_PER_SEC` slows its TX the same way. The kernel is tickless, so
  the rate costs no periodic interrupts; the demo tests check `k_msleep(100)` to 102 ms,
  which fails at 100 ticks/s.
- `irq_tx_complete` and error interrupts are left out on purpose: the status register
  has no "transmitter empty" bit, and errors are only sticky flags.

A TX interrupt and an interrupt-enable register would only come with a later hardware
revision, whose register map isn't bound by this one's (docs/APP_DEVELOPMENT.md, "Hardware
revisions and compatibility"); this revision doesn't add them.

`zephyr_workspace/irq_echo/` exercises all of it: main sleeps on a semaphore the RX
callback gives, and each line comes back upper-cased in one piece (longer than the TX
FIFO, so the timer path runs) with `rx=<n> drop=<n> err=<n>`. It fits the board without
printk (cbprintf alone is 1.5 KB; with it the app overflowed 14 KB by 1.8 KB). Tests:
`sim/emu/test_zephyr_irq_echo.py`, `sim-zephyr-irq-echo-rtl`, and on the board
`make test-hw-irq-echo` (`scripts/hw_irq_echo.py`, slot 3, not part of `test-hw`/dist).
