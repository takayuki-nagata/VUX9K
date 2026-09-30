# VUX9K Zephyr support

Board, SoC, UART driver and devicetree support for the VUX9K SoC on the Tang Nano 9K, as
a Zephyr module, plus a demo application (`app/`). Licensed under Apache-2.0 (`LICENSE`).

Tested with Zephyr v3.7.2 and Zephyr SDK 0.16.8 (`riscv64-zephyr-elf`).

## Building an application

```sh
west build -b vux9k <app dir> -- -DEXTRA_ZEPHYR_MODULES=<path to this directory>
```

`zephyr/module.yml` registers the board, SoC and devicetree roots, so no other option
is needed; for example `$ZEPHYR_BASE/samples/hello_world` builds as is. The slot image is
`build/zephyr/zephyr.bin`, which includes the initial `.data` (an XIP image copies it
from I-RAM to D-RAM at startup). Write it to a slot with
`vux_tool.py flash-sd build/zephyr/zephyr.bin --mode riscv --slot 1`.

## Boards

| Board | Memory | Runs on |
|---|---|---|
| `vux9k` | 14 KB ROM (I-RAM 0x0000-0x37FF), 8 KB RAM less the mailbox words | the board and the emulator |
| `vux9k/vux9k/ext` | 512 KB ROM, 256 KB RAM | **the emulator only** (`vux9k-emu --profile extended`); not real hardware |

The linker enforces the `vux9k` limits: an application that outgrows the board fails to
link. The CPU is RV32I + Zicsr + Zifencei, machine mode only, running at 18 MHz.

## Demo application (`app/`)

A Zephyr application whose logic is a Rust `no_std` staticlib (`app/rust_demo`), called
from `app/src/main.c`. It needs Rust 1.95.0 with the `riscv32i-unknown-none-elf` target.
Its `prj.conf` shows the size settings that fit an application into 14 KB.
