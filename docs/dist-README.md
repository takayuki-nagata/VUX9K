# VUX9K release

The VUX9K dual-ISA (RV32I and Hack) SoC for the Sipeed Tang Nano 9K: the bitstream, the
tools to put applications on the board, the Zephyr board support, and the emulator.
`MANIFEST.json` names the source commit and the tool versions; `SHA256SUMS` lists the
files' hashes. Source: https://github.com/takayuki-nagata/VUX9K

| Path | What |
|---|---|
| `bitstream/pack.fs` | FPGA bitstream (the first stage of the two-stage boot is inside) |
| `sd/VUX9K/STAGE2.BIN` | The second stage: copy it to the microSD card's FAT32 partition as `VUX9K\STAGE2.BIN` |
| `demos/` | Zephyr demo (RV32) and Hack demo images, for the emulator (the board starts applications with the next release's second stage) |
| `tools/vux_tool.py` | Talks to the boot stages over the serial port: card report, RAM boot, installing the second stage; makes v4 files and FAT32 card images |
| `tools/70-vux9k-board.rules` | udev rule that keeps ModemManager off the board (docs/APP_DEVELOPMENT.md, section 1) |
| `zephyr/vux9k-zephyr-bsp.tar.gz` | Zephyr board support (module) with a Rust application template |
| `hack/` | Hack demo's C source, a template for Hack applications |
| `emu/vux9k-emu`, `emu/vux9k_emu.abi3.so` | The emulator: command line and Python module (Linux x86_64) |
| `docs/APP_DEVELOPMENT.md` | The developer guide ("Hardware revisions and compatibility" says what stays compatible between releases) |

## Try it

On the board (needs openFPGALoader, Python 3 with pyserial, and a microSD card with one
FAT32 partition). Copy `sd/VUX9K/STAGE2.BIN` to the card as `VUX9K\STAGE2.BIN` (create
the directory `VUX9K` in its root), and insert it. If ModemManager runs on your host,
install `tools/70-vux9k-board.rules` first, as its comment shows; without it the board's
serial output can turn into noise after each bitstream load.

```sh
openFPGALoader -b tangnano9k bitstream/pack.fs
pip install -r tools/requirements.txt
python3 tools/vux_tool.py sd-info          # the card and STAGE2.BIN, as the first stage sees them
python3 tools/vux_tool.py monitor          # the first stage's banner, then the second stage's s2> prompt
```

Upgrading a board from v0.2.x: write this `bitstream/pack.fs` to the flash
(`openFPGALoader -b tangnano9k -f bitstream/pack.fs`) and prepare the card as above. The
v0.2.x slots on the card are no longer read. Section 2 of `docs/APP_DEVELOPMENT.md` has
the details.

On the emulator:

```sh
emu/vux9k-emu --no-firmware --load demos/zephyr-demo.bin --mode riscv --stdio
emu/vux9k-emu --no-firmware --load demos/hack-demo.bin --mode hack --stdio
```

(Ctrl-C stops the emulator.) Then read `docs/APP_DEVELOPMENT.md` to build your own
applications.

The SoC, firmware, tools and emulator are MIT-licensed (`LICENSE`); the Zephyr board
support is Apache-2.0 (the `LICENSE` in its archive); `THIRD_PARTY_LICENSES.txt` covers
third-party code in the binaries.
