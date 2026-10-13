# Developing applications for VUX9K

This guide is for writing applications that run on a Tang Nano 9K with the VUX9K
bitstream: RV32I applications on Zephyr (in C or Rust) and Hack applications in C. It
follows the usual path: put the bitstream and a demo on the board, build your own
application, test it on the emulator, then run it on the board.

Everything referred to here is in the release archive (`vux9k-<version>-linux-x86_64.tar.gz`);
paths are relative to its top directory. `MANIFEST.json` there names the commit it was
built from, the first stage's version and the tool versions it was built with.

Contents:
1. [Preparing the board](#1-preparing-the-board)
2. [The second stage and the demos](#2-the-second-stage-and-the-demos)
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

The bitstream contains the first stage of a two-stage boot in block RAM. At power-on (and
at every reset) it loads the second stage, `\VUX9K\STAGE2.BIN`, from the microSD card into
the PSRAM and starts it. A terminal on the board's serial port (115200 bps, 8N1) shows the
first stage's banner, then the second stage's `s2>` prompt; press `h` for its commands.
Holding button S2 while the board starts keeps the first stage at its own `s1>` prompt.

**microSD card.** The card needs one FAT32 partition (as most cards up to 32 GB come; an
SDXC card formatted exFAT must be reformatted as FAT32), a directory `VUX9K` in its root,
and in it the second stage: copy `sd/VUX9K/STAGE2.BIN` there with a PC. Insert the card
before power-on or reset. With no card, or no `STAGE2.BIN`, the first stage says why and
stays at its prompt.

## 2. The second stage and the demos

`tools/vux_tool.py` talks to the first and second stages over the serial port. It needs
pyserial (`pip install -r tools/requirements.txt`); `--port` defaults to finding the Tang
Nano 9K, or give it one, such as `--port /dev/ttyUSB1`.

```sh
python3 tools/vux_tool.py sd-info                                 # the card as the first stage sees it
python3 tools/vux_tool.py install-stage2 sd/VUX9K/STAGE2.BIN      # install it without a PC
python3 tools/vux_tool.py run sd/VUX9K/STAGE2.BIN                 # start it without writing the card
```

`install-stage2` starts the given second stage from the serial port (a RAM boot) and has
it write itself to the card as `STAGE2.BIN`, keeping the previous one as `STAGE2.OLD`: when
`STAGE2.BIN` is missing or broken, the first stage starts `STAGE2.OLD`. `run` is the RAM boot
alone, for trying a second stage or recovering a card. `monitor` opens a plain terminal.

The second stage in this release is provisional: it installs second stages, but it doesn't
start applications yet. That comes with the next one, the file manager and launcher. Until
then, run applications on the emulator (section 4); `demos/` holds the Zephyr and Hack
demos for it.

**Upgrading from v0.2.x.** v0.2.x kept applications and a Boot Manager in slots between the
card's partition table and its first partition; this release reads only the FAT32 partition
and ignores them. Write the new bitstream to the flash (section 1), prepare the card as above
and rebuild applications (section 3).

## 3. Building an application

The CPU is RV32I with Zicsr and Zifencei, machine mode only, at 18 MHz, with 16 KB of
instruction RAM, 8 KB of data RAM and 8 MB of PSRAM. The Zephyr board `vux9k` still links
applications into 14 KB of the instruction RAM and the data RAM except its last 8 bytes,
as on v0.2.x; it moves to the PSRAM with the launcher.
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

`build/zephyr/zephyr.bin` is the image for the emulator (`--mode riscv`).
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
hcc -r hack/main.c -o app.bin    # the image for the emulator (--mode hack)
```

Hack mode sees the UART at `0x6000`-`0x6003`, the GPIO at `0x6004`-`0x600F` and 2K words
of RAM ([Hack Mode Address Space](../README.md#hack-mode-address-space-16-bit-word-addresses)).

## 4. Testing on the emulator

`emu/vux9k-emu` models the SoC cycle for cycle: both ISAs, the memories and every
peripheral, with the UART's and SD card's real timing. Start an application directly,
without the boot stages, from its image and ISA:

```sh
emu/vux9k-emu --no-firmware --load build/zephyr/zephyr.bin --mode riscv --stdio
emu/vux9k-emu --no-firmware --load app.bin --mode hack --stdio
```

`--mode` starts the CPU in that ISA with the image at address 0 of the instruction RAM, and
refuses an image larger than the 14 KB the board `vux9k` links into.

- `--stdio` connects the UART to the terminal (input goes line by line); `--tcp PORT`
  serves it on `localhost:PORT` instead, for a terminal program.
- `--until TEXT` stops with exit status 0 once the output contains `TEXT`, and
  `--cycles N` stops after N cycles (exit status 1 if `--until` hasn't matched): together a
  test for scripts. Interactive runs keep to 18 MHz; `--speed 0` runs as fast as possible.
- `--trace FILE` logs every executed instruction.
- `--profile extended` gives 512 KB / 256 KB for the `vux9k/vux9k/ext` Zephyr board.
  **Nothing that needs it runs on the board.**

Started this way, an application finds its image at address 0, the CPU in the ISA of
`--mode`, the data RAM zeroed, and an empty SD card (a card image can be given with `--sd
IMAGE`; `vux_tool.py mkfat` makes one).

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

Starting applications on the board comes with the second stage's launcher (section 2).
What applies to anything that runs there:

Writing `0x5A5A_A55A` to `0x4000_300C` resets the CPU only, like S1, and starts the first
stage again, which boots the card. A Hack program writes `0x0000_A55A` to `0x600C` instead,
formed by addition such as `0x255A + 0x4000 + 0x4000` (an `@n` holds at most `0x7FFF`).
The UART, timer, GPIO and SD SPI master keep their state across it
([Memory & MMIO Register Map](../README.md#memory--mmio-register-map)).

**If output stops after ~128 bytes** or arrives seconds late: the Tang Nano 9K's on-board
USB-UART bridge (BL702) does this when another full-speed USB device is busy on the same
USB hub. It is on the host side, not in the SoC. Plug the board into its own USB port or
hub.

**If the output is noise** (about half the expected bytes, mostly non-ASCII), usually in
the first session after loading the bitstream: the bridge runs at a baud rate
ModemManager set. Install the udev rule (section 1).

## 6. Hardware revisions and compatibility

This guide and the README describe the hardware revision with the PSRAM and the two-stage
boot: the RV32 and Hack address spaces and their registers, the CSRs, the VUX9 v4 file
header, `\VUX9K\STAGE2.BIN` on a FAT32 card, and the state a program starts in. Within the
releases of one minor version, a patch release doesn't break applications or SD cards: an
application that uses only what is documented, and a card prepared for an earlier patch
release, keep working. (The v0.2.x releases, the previous revision, kept that promise for
their slots, which this revision no longer reads.)

A later hardware revision makes no such promise. Its memory map, registers, file format,
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
[Two-stage boot](../README.md#two-stage-boot-the-first-stage-and-the-sd-card),
[VUX9 file header](../README.md#vux9-file-header-64-bytes-v4) (`vux_tool.py mkv4` writes it),
[the first stage's prompt](../README.md#2-the-first-stages-prompt).

**Licenses:** the VUX9K SoC, firmware, tools and emulator are MIT (`LICENSE`); the Zephyr
board support package is Apache-2.0 (its `LICENSE`). `THIRD_PARTY_LICENSES.txt` covers the
third-party code in the binaries.
