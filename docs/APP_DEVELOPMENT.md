# Developing applications for VUX9K

This guide is for writing applications that run on a Tang Nano 9K with the VUX9K
bitstream: RV32I applications on Zephyr (in C or Rust) and Hack applications in C. It
follows the usual path: put the bitstream and a demo on the board, build your own
application, test it on the emulator, then run it on the board.

Everything referred to here is in the release archive (`vux9k-<version>-linux-x86_64.tar.gz`);
paths are relative to its top directory. `MANIFEST.json` there names the commit it was
built from, the Boot Manager version and the tool versions it was built with.

Contents:
1. [Preparing the board](#1-preparing-the-board)
2. [Running the demos and updating the Boot Manager](#2-running-the-demos-and-updating-the-boot-manager)
3. [Building an application](#3-building-an-application)
4. [Testing on the emulator](#4-testing-on-the-emulator)
5. [Running on the board](#5-running-on-the-board)
6. [Hardware revisions and compatibility](#6-hardware-revisions-and-compatibility)
7. [Reference](#7-reference)

## 1. Preparing the board

**Host requirements:** Linux on x86_64 (the emulator binary; glibc 2.35 or later),
Python 3.10 or later, and [openFPGALoader](https://github.com/trabucayre/openFPGALoader)
(packaged by most distributions, and part of the OSS CAD Suite). Your user needs access to
the board's USB device; openFPGALoader's documentation lists the udev rule.

**ModemManager.** If it runs on your host (most desktop distributions start it), install
`tools/70-vux9k-board.rules`, as its comment shows, so that it leaves the board alone.
ModemManager probes each new serial port, and openFPGALoader hands the board's JTAG port
back as a new one after every load. The probe sets 57600 baud, and the board's USB bridge
applies that rate to the board's serial port as well: the next session shows noise instead
of the board's output. `tools/vux_tool.py` warns when ModemManager can probe the board.

**Bitstream.** Load it into the FPGA's SRAM (lost at power-off, handy while trying a
release) or write it to the on-board flash (loaded at every power-on):

```sh
openFPGALoader -b tangnano9k bitstream/pack.fs      # SRAM
openFPGALoader -b tangnano9k -f bitstream/pack.fs   # flash
```

The bitstream contains the Boot Manager and the Resident Loader in block RAM. After
loading it, a terminal on the board's serial port (115200 bps, 8N1) shows the Boot
Manager's `vux>` prompt; press `h` for its commands.

**microSD card.** Applications live in ten 32 KB slots on the card, in LBA 64-703: the
gap between the partition table (LBA 0) and the first partition, which no filesystem
uses. A card formatted with FAT or exFAT works as it is and keeps its files, as long as
its first partition starts at LBA 704 or later; cards formatted the usual way start it at
LBA 2048 or beyond (`fdisk -l` shows the start). Sector 0 must end with the `55 AA` boot
signature, which any partitioned card has. Insert the card before power-on or reset.

## 2. Running the demos and updating the Boot Manager

`tools/vux_tool.py` talks to the Boot Manager over the serial port. It needs pyserial
(`pip install -r tools/requirements.txt`); `--port` defaults to finding the Tang Nano 9K,
or give it one, such as `--port /dev/ttyUSB1`.

```sh
python3 tools/vux_tool.py flash-sd demos/zephyr-demo.bin --mode riscv --slot 1 --name "Zephyr demo"
python3 tools/vux_tool.py flash-sd demos/hack-demo.bin   --mode hack  --slot 2 --name "Hack demo"
python3 tools/vux_tool.py list-slots
python3 tools/vux_tool.py boot --slot 1     # prints the Zephyr demo's output
```

`boot --slot N` is the Boot Manager's key `N`; button S2 at the prompt starts slot 1.
`monitor` opens a plain terminal. An application replaces the Boot Manager in
instruction RAM, so getting back to the Boot Manager takes reconfiguring the FPGA:
power-cycle the board or run `python3 tools/vux_tool.py reset` (both load the bitstream
from flash), or load `bitstream/pack.fs` into SRAM again. The reset button S1 resets
the SoC only, which restarts the application.

**Updating the Boot Manager.** Slot 0 holds a Boot Manager on the card. Whenever the
Boot Manager starts, it reads slot 0's header and installs that image in its place when
the header's version is greater than its own (`boot_manager_version` in
`MANIFEST.json`); holding S2 while it starts skips this. So a
newer Boot Manager reaches a board without rebuilding the bitstream:

```sh
python3 tools/vux_tool.py flash-sd boot-manager.bin --mode riscv --slot 0 --name "Boot Manager" \
    --version <boot_manager_version from MANIFEST.json>
```

The header version is whatever `--version` says (default 1, which never installs), so
pass the Boot Manager's own version. With the bitstream of the same release the two are
equal and nothing is installed; when a newer one is, the Boot Manager prints
`[UPDATE] Verified valid Boot Manager update` as it starts.

**Upgrading from an earlier release.** Write the new release's `bitstream/pack.fs` to the
flash (section 1) and use its `tools/vux_tool.py`; the slots already on the card keep
working. Then write its `boot-manager.bin` to slot 0 as above, with its version. A Boot
Manager installed from slot 0 also runs on an older bitstream, but it can use only the
hardware that bitstream has: on the v0.1.0 bitstream, the v0.2.0 Boot Manager reads the
card at about 400 kHz instead of 3 MHz. To go back to an older release, write its Boot
Manager to slot 0 before loading its bitstream; otherwise the older Boot Manager installs
the newer one from slot 0 every time it starts.

## 3. Building an application

The CPU is RV32I with Zicsr and Zifencei, machine mode only, at 18 MHz, with 16 KB of
instruction RAM and 8 KB of data RAM. An application may use 14 KB of the instruction RAM
(the top 2 KB hold the Resident Loader) and the data RAM except its last 8 bytes.
Misaligned loads and stores trap, and so does any CSR that isn't implemented
([RV32 CSRs](../README.md#rv32-csrs) in the repository's README lists them).

### Zephyr (C or Rust)

Install Zephyr and its SDK in the versions in `MANIFEST.json` (`pins.zephyr`,
`pins.zephyr_sdk`; the Zephyr SDK needs only the `riscv64-zephyr-elf` toolchain), as in
Zephyr's Getting Started Guide. Then unpack the board support package and build:

```sh
tar -xzf zephyr/vux9k-zephyr-bsp.tar.gz            # -> vux9k-zephyr-bsp/
west build -b vux9k $ZEPHYR_BASE/samples/hello_world -- \
    -DEXTRA_ZEPHYR_MODULES=$PWD/vux9k-zephyr-bsp
```

`build/zephyr/zephyr.bin` is the image for a slot and for the emulator (`--mode riscv`).
Use it as `west build` writes it: it carries the initial values of `.data`, which the
image copies from instruction RAM to data RAM at startup.

- **C:** any Zephyr application; start from `samples/hello_world`. `hello_world` with
  default settings takes 13 KB of the 14 KB, so larger applications need Zephyr's size
  options.
- **Rust:** `vux9k-zephyr-bsp/app/` is a template: a Zephyr application whose logic is a
  Rust `no_std` staticlib (`app/rust_demo`), called from `app/src/main.c` through small
  C wrappers for Zephyr's API. It needs Rust (`pins.rust`) with the target
  `riscv32i-unknown-none-elf` (`rustup target add riscv32i-unknown-none-elf`). Its
  `prj.conf` has the settings that make it fit in 14 KB (minimal libc, `printk` without
  `printf`, small stacks).
- The board `vux9k` enforces the limits at link time: an image that outgrows 14 KB / 8 KB
  fails to link. The board `vux9k/vux9k/ext` (512 KB / 256 KB) is for the emulator's
  `extended` profile only; **it does not run on the board**.
- The console is the SoC's UART. Its driver also has the interrupt-driven API
  (`CONFIG_UART_INTERRUPT_DRIVEN`): RX on the machine external interrupt, TX refilled
  from a kernel timer since the UART has no TX interrupt (`vux9k-zephyr-bsp/README.md`,
  "UART"; `vux9k-zephyr-bsp/irq_echo/` is an example). The system timer is the SoC timer
  (`mtime`, 18 ticks per microsecond); both boards run the kernel tickless at 1000
  ticks/s, so timeouts have 1 ms resolution.

### Hack (C)

Hack applications are compiled with [hack_tools](https://github.com/takayuki-nagata/hack_tools)
(`pins.hack_tools`): `hcc` (from the `m2h` Python package) translates C through
msp430-gcc (`pins.msp430_gcc`, TI's MSP430 GCC) into Hack assembly and assembles it with
`has`. Install the three: `has` from the hack_tools release archive (on `PATH`),
`pip install https://github.com/takayuki-nagata/hack_tools/releases/download/<version>/m2h-<version without v>.tar.gz`,
and MSP430 GCC with `msp430-gcc` on `PATH`.

`hack/` holds the Hack demo's source as a template (`main.c` includes `uart.c`, which
writes to the UART at Hack address 24576):

```sh
hcc -r hack/main.c -o app.bin    # the image for a slot and for the emulator (--mode hack)
```

Hack mode sees the UART at `0x6000`-`0x6003`, the GPIO at `0x6004`-`0x600F` and 2K words
of RAM ([Hack Mode Address Space](../README.md#hack-mode-address-space-16-bit-word-addresses)).

## 4. Testing on the emulator

`emu/vux9k-emu` models the SoC cycle for cycle: both ISAs, the memories and every
peripheral, with the UART's and SD card's real timing. Start an application directly,
without the Boot Manager, from the same image and `--mode` you give `vux_tool.py flash-sd`:

```sh
emu/vux9k-emu --no-firmware --load build/zephyr/zephyr.bin --mode riscv --stdio
emu/vux9k-emu --no-firmware --load app.bin --mode hack --stdio
```

`--mode` starts the CPU in that ISA, as the Resident Loader does with the slot header's,
and refuses an image larger than the 14 KB a slot application may have.

- `--stdio` connects the UART to the terminal (input goes line by line); `--tcp PORT`
  serves it on `localhost:PORT` instead, for a terminal program.
- `--until TEXT` stops with exit status 0 once the output contains `TEXT`, and
  `--cycles N` stops after N cycles (exit status 1 if `--until` hasn't matched): together a
  test for scripts. Interactive runs keep to 18 MHz; `--speed 0` runs as fast as possible.
- `--trace FILE` logs every executed instruction.
- `--profile extended` gives 512 KB / 256 KB for the `vux9k/vux9k/ext` Zephyr board.
  **Nothing that needs it runs on the board.**

Started this way, an application finds what the Resident Loader leaves on the board: its
image at address 0, the CPU in the ISA of `--mode`, and the data RAM zeroed. The
difference is the SD card, which is empty (a card image can be given with `--sd IMAGE`).

**From Python.** `emu/vux9k_emu.abi3.so` is the emulator as a Python module (CPython 3.10
or later, Linux x86_64). Put `emu/` on `sys.path`:

```python
import sys
sys.path.insert(0, "emu")
import vux9k_emu

soc = vux9k_emu.Soc("real")
with open("demos/zephyr-demo.bin", "rb") as f:
    soc.load_app(f.read(), "riscv")
end = soc.run_until_tx(b"All Rust application tasks finished successfully!", 40_000_000)
assert end is not None, soc.uart_received()
print(f"demo finished after {soc.cycle} cycles ({soc.cycle / 18e6:.2f} s at 18 MHz)")
```

Useful methods of `Soc`: `load_app(image, mode)` (`mode` `"riscv"` or `"hack"`, as
`--load --mode`), `reset()` (power-on reset; load the image again after it),
`run(max_cycles)`,
`run_until_tx(needle, max_cycles)` (returns the output position after `needle`, or
`None`), `uart_send(bytes)`, `uart_received()` (output so far),
`set_button(pressed)`, and the properties `leds`, `cycle`, `pc`, `regs`,
`mcause`/`mepc`/`mtval`.

## 5. Running on the board

Write the application to a free slot (1-9) and start it. The Resident Loader reads the
slot once to check it (including the payload's CRC32, which `flash-sd` writes into the
header) before loading it; its errors are listed in the README
([Resident Loader and the mailbox](../README.md#3-resident-loader-and-the-mailbox)):

```sh
python3 tools/vux_tool.py flash-sd build/zephyr/zephyr.bin --mode riscv --slot 3 --name "my app"
python3 tools/vux_tool.py boot --slot 3
```

At start, an application finds:
- its image at address 0 of the instruction RAM, and the CPU starting there in the ISA of
  the slot header;
- the data RAM zeroed, except for the last 8 bytes (`0x2000_1FF8`-`0x2000_1FFF`), which
  belong to the loaders: don't use them;
- the UART, timer, GPIO and SD SPI master as the Boot Manager left them
  ([Memory & MMIO Register Map](../README.md#memory--mmio-register-map)); the SD card is
  initialized and its SCLK at 3 MHz. Interrupts are
  the timer (`MTI`) and UART receive (`MEI`, while the receive FIFO holds data).

Writing `0x5A5A_A55A` to `0x4000_300C` resets the CPU only, like S1: it restarts the
application (the instruction RAM now holds it). A Hack application writes `0x0000_A55A`
to `0x600C` instead, formed by addition such as `0x255A + 0x4000 + 0x4000` (an `@n` holds
at most `0x7FFF`). After such a reset the CPU guesses the ISA from the first instruction
word ([ISA selection](../README.md#hack-mode-address-space-16-bit-word-addresses) in the
README) unless the application first writes it to GPIO register `0x8` (`0x101` RV32,
`0x100` Hack). To return to the Boot Manager, reconfigure the FPGA as in section 2.

**If output stops after ~128 bytes** or arrives seconds late: the Tang Nano 9K's on-board
USB-UART bridge (BL702) does this when another full-speed USB device is busy on the same
USB hub. It is on the host side, not in the SoC. Plug the board into its own USB port or
hub.

**If the output is noise** (about half the expected bytes, mostly non-ASCII), usually in
the first session after loading the bitstream: the bridge runs at a baud rate
ModemManager set. Install the udev rule (section 1).

## 6. Hardware revisions and compatibility

This guide and the README describe this hardware revision of the SoC, which the v0.2.x
releases implement: the RV32 and Hack address spaces and their registers, the CSRs, the
slot header, the SD card's sector map, the Resident Loader and its mailbox, and the state
an application starts in. Within those releases, a patch release doesn't break
applications or slots: an application that uses only what is documented, and a slot
written with an earlier v0.2.x `vux_tool.py`, keep running.

A later hardware revision makes no such promise. Its memory map, registers, slot format,
SD card layout and loaders may all change, and its release says how to rebuild and
rewrite applications for it. Applications that reach the hardware only through Zephyr's
APIs are the easiest to move: they may need no more than a rebuild for that revision's
board. Applications that use addresses or registers directly may need changes.

Even on this revision, only the documented addresses, registers and bits are an
interface. The address aliases, what an undocumented address or register offset reads,
and the absence of access faults are how the hardware happens to behave
([Memory & MMIO Register Map](../README.md#memory--mmio-register-map)); don't rely on
them.

## 7. Reference

In the repository's [README](../README.md):
[Memory & MMIO Register Map](../README.md#memory--mmio-register-map),
[RV32 CSRs](../README.md#rv32-csrs),
[MicroSD Card Sector Map](../README.md#microsd-card-sector-map-multi-slot-mbr-gap-boot),
[VUX9 Boot Header](../README.md#vux9-boot-header-64-bytes-v3) (written by
`vux_tool.py flash-sd`; you choose only the name and the version),
[Boot Manager CLI](../README.md#2-on-chip-bare-metal-boot-manager-cli).

**Licenses:** the VUX9K SoC, firmware, tools and emulator are MIT (`LICENSE`); the Zephyr
board support package is Apache-2.0 (its `LICENSE`). `THIRD_PARTY_LICENSES.txt` covers the
third-party code in the binaries.
