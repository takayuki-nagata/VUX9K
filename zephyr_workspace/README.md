# VUX9K Zephyr support

Board, SoC, UART driver and devicetree support for the VUX9K SoC on the Tang Nano 9K, as
a Zephyr module, plus a demo application (`app/`). Licensed under Apache-2.0 (`LICENSE`).

Tested with Zephyr v3.7.2 and Zephyr SDK 0.16.8 (`riscv64-zephyr-elf`).

## Building an application

```sh
west build -b vux9k <app dir> -- -DEXTRA_ZEPHYR_MODULES=<path to this directory>
```

`zephyr/module.yml` registers the board, SoC and devicetree roots, so no other option
is needed; for example `$ZEPHYR_BASE/samples/hello_world` builds as is. The image is
`build/zephyr/zephyr.bin`, which includes the initial `.data` (an XIP image copies it
from the image to its RAM at startup). Run it on the emulator with
`vux9k-emu --no-firmware --load build/zephyr/zephyr.bin --mode riscv --stdio` (it loads into
the PSRAM at 0x8000_0000). On the board, the second stage starts it from the microSD card:
wrap it as a VUX9 v4 file (`vux_tool.py mkv4 build/zephyr/zephyr.bin APP.BIN`: an RV32 image
at 0x8000_0000 by default), put it in the card's `\VUX9K\APPS\` (`vux_tool.py put APP.BIN
VUX9K/APPS/APP.BIN`, or with a PC), and `run` it at the `s2>` prompt (`vux_tool.py run-app
VUX9K/APPS/APP.BIN`). It runs in place in the PSRAM; the first stage stays in I-RAM, and
button S1 (the board's reset) goes back to it and the boot.

## Board

| Board | Memory | Runs on |
|---|---|---|
| `vux9k` | the PSRAM: 4 MB ROM from 0x8000_0000 (run in place, XIP; the second stage's `run` loads the image there), 3 MB RAM from 0x8040_0000 | the board (started by the second stage's `run`) and the emulator |

The CPU is RV32I + Zicsr + Zifencei, machine mode only, running at 18 MHz. On `vux9k`
every fetch and data access waits ~35 cycles (the PSRAM has no cache in front of it), so
code runs 15-20 times slower than from the SoC's block RAMs, which `vux9k` leaves to the
first stage. Big applications fit `vux9k` as well: bc_clone_rs on Zephyr (~260 KB, with a
64 KB heap and a 32 KB stack) runs there, slowly. The board runs the kernel tickless at
1000 ticks/s, so timeouts have 1 ms resolution.

## UART

`vux9k,uart` supports polling and, with `CONFIG_UART_INTERRUPT_DRIVEN=y`, the
interrupt-driven API (`uart_irq_*`, `uart_fifo_*`). The SoC raises the machine external
interrupt (cause 11) while the 32-byte RX FIFO holds data; it is level-triggered and has
no enable bit in the UART, so `uart_irq_rx_disable()` masks it in `mie`. The UART has no
TX interrupt: `uart_irq_tx_enable()` calls the callback at once, and a kernel timer calls
it again every 700 us (rounded up to a tick, 1 ms at the boards' 1000 ticks/s) while TX
stays enabled, which keeps up with 115200 baud. A lower `CONFIG_SYS_CLOCK_TICKS_PER_SEC`
slows it down: one 32-byte refill per tick. `uart_irq_tx_complete()`
is not supported (the status register has no "transmitter empty" bit), nor are error
interrupts. `irq_echo/` is an example.

## Demo application (`app/`)

A Zephyr application whose logic is a Rust `no_std` staticlib (`app/rust_demo`), called
from `app/src/main.c`. It needs Rust 1.95.0 with the `riscv32i-unknown-none-elf` target.
Its `prj.conf` shows size settings that keep an application small (13 KB), and so quick
to load from the card.

## Interrupt-driven UART echo (`irq_echo/`)

A C application that sends each line it receives back in upper case through the
interrupt-driven UART API, followed by `rx=<bytes> drop=<bytes lost in the app>
err=<uart_err_check()>`. Its main thread sleeps until the UART callback wakes it, and a
line longer than 32 bytes goes through the driver's TX retry.
