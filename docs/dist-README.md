# VUX9K release

The VUX9K dual-ISA (RV32I and Hack) SoC for the Sipeed Tang Nano 9K: the bitstream, the
tools to put applications on the board, the Zephyr board support, and the emulator.
`MANIFEST.json` names the source commit and the tool versions; `SHA256SUMS` lists the
files' hashes. Source: https://github.com/takayuki-nagata/VUX9K

| Path | What |
|---|---|
| `bitstream/pack.fs` | FPGA bitstream (the Boot Manager and Resident Loader are inside) |
| `boot-manager.bin` | Boot Manager image, to update it from the SD card's slot 0 |
| `demos/` | Zephyr demo (RV32) and Hack demo images, for the board and the emulator alike |
| `tools/vux_tool.py` | Writes applications to the SD card and starts them, over the serial port |
| `tools/70-vux9k-board.rules` | udev rule that keeps ModemManager off the board (docs/APP_DEVELOPMENT.md, section 1) |
| `zephyr/vux9k-zephyr-bsp.tar.gz` | Zephyr board support (module) with a Rust application template |
| `hack/` | Hack demo's C source, a template for Hack applications |
| `emu/vux9k-emu`, `emu/vux9k_emu.abi3.so` | The emulator: command line and Python module (Linux x86_64) |
| `docs/APP_DEVELOPMENT.md` | The developer guide |

## Try it

On the board (needs openFPGALoader, Python 3 with pyserial, and a FAT-formatted microSD
card in the board):

```sh
openFPGALoader -b tangnano9k bitstream/pack.fs
pip install -r tools/requirements.txt
python3 tools/vux_tool.py flash-sd demos/zephyr-demo.bin --mode riscv --slot 1 --name "Zephyr demo"
python3 tools/vux_tool.py boot --slot 1
```

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
