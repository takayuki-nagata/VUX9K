# VUX9K release

The VUX9K dual-ISA (RV32I and Hack) SoC for the Sipeed Tang Nano 9K: the bitstream, the
tools to put applications on the board, the Zephyr board support, and the emulator.
`MANIFEST.json` names the source commit and the tool versions; `SHA256SUMS` lists the
files' hashes. Source: https://github.com/takayuki-nagata/VUX9K

| Path | What |
|---|---|
| `bitstream/pack.fs` | FPGA bitstream (the first stage of the two-stage boot is inside) |
| `sd/VUX9K/STAGE2.BIN` | The second stage, file manager and application launcher: copy it to the microSD card's FAT32 partition as `VUX9K\STAGE2.BIN`, or install it over the serial port (`vux_tool.py install-stage2`) |
| `sd/VUX9K/APPS/` | The demos as v4 files for the card's `VUX9K\APPS\`, started by the second stage's `run`: `HELLO.BIN`, `ZEPHYR.BIN`, `IRQECHO.BIN`, `BC.BIN`, `MPY.BIN` (Zephyr; `BC.BIN` is the bc calculator, ~260 KB, `MPY.BIN` MicroPython's REPL with bc_clone_py, ~357 KB) in the PSRAM, `HACK.BIN` (Hack) in Hack's segments in the PSRAM |
| `demos/` | Zephyr demo (RV32) and Hack demo images, for the emulator's `--load` |
| `tools/vux_tool.py` | Talks to the boot stages over the serial port: card report, RAM boot, installing the second stage, putting files on the card and starting applications; makes v4 files and FAT32 card images |
| `tools/70-vux9k-board.rules` | udev rule that keeps ModemManager off the board (docs/APP_DEVELOPMENT.md, section 1) |
| `zephyr/vux9k-zephyr-bsp.tar.gz` | Zephyr board support (module) with a Rust application template |
| `hack/` | Hack demo's C source, a template for Hack applications |
| `emu/vux9k-emu`, `emu/vux9k_emu.abi3.so` | The emulator: command line and Python module (Linux x86_64) |
| `emu/firmware/` | The first stage's ROM image (`firmware.hex`), for booting a card image on the emulator (`--firmware emu/firmware --sd IMAGE`) |
| `docs/APP_DEVELOPMENT.md` | The developer guide ("Hardware revisions and compatibility" says what stays compatible between releases) |

## Try it

On the board (needs openFPGALoader, Python 3 with pyserial, and a microSD card with one
FAT32 partition). Copy `sd/VUX9K/` to the card as `VUX9K\` with a PC and insert it, or
insert the card as it is and install the second stage and a demo over the serial port as
below. If ModemManager runs on your host, install `tools/70-vux9k-board.rules` first, as its
comment shows; without it the board's serial output can turn into noise after each
bitstream load.

```sh
openFPGALoader -b tangnano9k bitstream/pack.fs
pip install -r tools/requirements.txt
python3 tools/vux_tool.py install-stage2 sd/VUX9K/STAGE2.BIN   # unless the card has it (makes VUX9K\)
python3 tools/vux_tool.py sd-info          # the card and STAGE2.BIN, as the first stage sees them
python3 tools/vux_tool.py monitor          # sd-info left s1>: b boots the second stage (s2>): help, ls, ...
python3 tools/vux_tool.py mkdir VUX9K/APPS # unless the card has it
python3 tools/vux_tool.py put sd/VUX9K/APPS/HELLO.BIN VUX9K/APPS/HELLO.BIN
python3 tools/vux_tool.py run-app VUX9K/APPS/HELLO.BIN
```

Upgrading a board from v0.2.x: write this `bitstream/pack.fs` to the flash
(`openFPGALoader -b tangnano9k -f bitstream/pack.fs`) and prepare the card as above; a v0.2.x
card whose first partition is FAT32 works as it is, and its slots are left alone, no longer
read. Applications built for
v0.2.x must be rebuilt. Section 2 of `docs/APP_DEVELOPMENT.md` has the steps.

On the emulator:

```sh
emu/vux9k-emu --no-firmware --load demos/zephyr-demo.bin --mode riscv --stdio
emu/vux9k-emu --no-firmware --load demos/hack-demo.bin --mode hack --stdio
```

(Ctrl-C stops the emulator. `--mode riscv` puts the image in the PSRAM at `0x8000_0000`,
where the Zephyr board links and the second stage's `run` loads it; `--mode hack` in Hack's
instruction segment, also in the PSRAM.) Then read `docs/APP_DEVELOPMENT.md` to build your own
applications.

The SoC, firmware, tools and emulator are MIT-licensed (`LICENSE`); the Zephyr board
support is Apache-2.0 (the `LICENSE` in its archive); `THIRD_PARTY_LICENSES.txt` covers
third-party code in the binaries.
