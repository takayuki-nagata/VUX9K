# VUX9K Zephyr support

Board, SoC and devicetree support for the VUX9K SoC on the Tang Nano 9K, as
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
VUX9K/APPS/APP.BIN`). It runs in place in the PSRAM; the first stage stays in the boot ROM, and
button S1 (the board's reset) goes back to it and the boot.

## Board

| Board | Memory | Runs on |
|---|---|---|
| `vux9k` | the PSRAM: 4 MB ROM from 0x8000_0000 (run in place, XIP; the second stage's `run` loads the image there), 3 MB RAM from 0x8040_0000 | the board (started by the second stage's `run`) and the emulator |

The CPU is RV32IM + Zicsr + Zifencei, machine mode only, running at 18 MHz. On `vux9k` a
fetch that misses the 16 KB instruction cache (8-word lines) waits ~49 cycles and a load or store that
misses the 2 KB data cache (write-back, 8-word lines) ~49 (~99 when it writes a line back
first); hits, stores included, take no longer than from the SoC's block RAMs, which
`vux9k` leaves to the first stage. Big applications fit `vux9k` as well: bc_clone_rs on
Zephyr (~260 KB, with a 64 KB heap and a 32 KB stack) runs there (its CPI to the first
prompt was ~10 with the caches before the data cache became write-back, ~42 without). The board runs the kernel tickless at
1000 ticks/s, so timeouts have 1 ms resolution.

## UART

The SoC's UART has LiteX liteuart's registers, so the board uses Zephyr's own driver,
`litex,uart0` (`uart_liteuart.c`), with polling and, with `CONFIG_UART_INTERRUPT_DRIVEN=y`,
the interrupt-driven API (`uart_irq_*`, `uart_fifo_*`). Its RX and TX events raise the
machine external interrupt (cause 11) as EV_ENABLE enables them. Unlike LiteX's, they are
levels: RX while the 32-byte RX FIFO holds data, TX while the 32-byte TX FIFO has room. So
a callback that has nothing more to send calls `uart_irq_tx_disable()`, or the interrupt
comes back at once. Once EV_ENABLE isn't 0, EV_PENDING is EV_STATUS masked
by it, so `uart_irq_is_pending()` sees only enabled events (with EV_ENABLE 0 it shows
EV_STATUS, which Linux's liteuart polls without an interrupt). The driver names the interrupt with a priority cell, which the hart's
`riscv,cpu-intc` doesn't have: the board's dts passes cause 11 through a two-cell node,
`vux9k,meip-intc` (a binding with no driver). The UART has no error flags (a byte that
arrives with the RX FIFO full is lost, a frame with a low stop bit is received), and the
driver no `err_check`: `uart_err_check()` returns `-ENOSYS`. Running from the PSRAM, the RX
handler keeps up with input sent back to back at 115200 baud thanks to the instruction
cache in front of it (without it, it took about four byte times per byte and overran the
32-byte RX FIFO). `irq_echo/` is an example.

## Demo application (`app/`)

A Zephyr application whose logic is a Rust `no_std` staticlib (`app/rust_demo`), called
from `app/src/main.c`. It needs Rust 1.95.0 with the `riscv32im-unknown-none-elf` target.
Its `prj.conf` shows size settings that keep an application small (13 KB), and so quick
to load from the card.

## Interrupt-driven UART echo (`irq_echo/`)

A C application that sends each line it receives back in upper case through the
interrupt-driven UART API, followed by `rx=<bytes> drop=<bytes lost in the app>`. Its main
thread sleeps until the UART callback wakes it, and a line longer than 32 bytes is refilled
from the TX interrupt.
