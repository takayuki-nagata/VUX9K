# VUX9K

[![CI](https://img.shields.io/github/actions/workflow/status/takayuki-nagata/VUX9K/ci.yml?branch=main&event=push&label=CI)](https://github.com/takayuki-nagata/VUX9K/actions/workflows/ci.yml?query=branch%3Amain+event%3Apush)
[![nightly](https://img.shields.io/github/actions/workflow/status/takayuki-nagata/VUX9K/ci.yml?branch=main&event=schedule&label=nightly)](https://github.com/takayuki-nagata/VUX9K/actions/workflows/ci.yml?query=event%3Aschedule)
[![release](https://img.shields.io/github/v/release/takayuki-nagata/VUX9K)](https://github.com/takayuki-nagata/VUX9K/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue)](#license)
<br>
[![HDL: Veryl](https://img.shields.io/badge/HDL-Veryl-orange)](https://github.com/veryl-lang/veryl)
[![ISA: RISC-V RV32I](https://img.shields.io/badge/ISA-RISC--V%20RV32I-283272?logo=riscv)](https://riscv.org/)
[![ISA: Nand2Tetris Hack](https://img.shields.io/badge/ISA-Nand2Tetris%20Hack-555555)](https://www.nand2tetris.org/)
[![board: Tang Nano 9K](https://img.shields.io/badge/board-Tang%20Nano%209K%20%28GW1NR--9%29-blue)](https://wiki.sipeed.com/hardware/en/tang/Tang-Nano-9K/Nano-9K.html)
[![RTOS: Zephyr](https://img.shields.io/badge/RTOS-Zephyr-7929d2?logo=zephyrproject)](https://www.zephyrproject.org/)
[![firmware: Rust](https://img.shields.io/badge/firmware-Rust-b7410e?logo=rust)](https://www.rust-lang.org/)

**VUX9K** (Veryl Unified eXecution on Tang Nano 9K) is an open-source **Dual-ISA (RISC-V RV32I & Nand2Tetris Hack 16-bit) System-on-Chip (SoC)** written 100% in **[Veryl](https://github.com/veryl-lang/veryl)** targeting the **Sipeed Tang Nano 9K** FPGA board (Gowin GW1NR-9).

The SoC features a multi-cycle Unified CPU core capable of seamlessly executing both standard **32-bit RISC-V RV32I** instructions and **16-bit Nand2Tetris Hack** machine code, integrated with a hardware MicroSD SPI master (3 MHz after card init), full-duplex UART with a receive interrupt, 64-bit CLINT timer, GPIO, 8 MB of PSRAM, and a **two-stage boot**: a bare-metal Rust first stage in the bitstream loads the second stage, `\VUX9K\STAGE2.BIN`, from the MicroSD card's FAT32 partition into the PSRAM.

Around it: **Zephyr RTOS** board support for the real board (C or Rust applications, an interrupt-driven UART driver), a **cycle-accurate Rust emulator** checked instruction by instruction, cycle counts included, against the RTL, and [releases](https://github.com/takayuki-nagata/VUX9K/releases) with everything needed to write applications without building the SoC ([`docs/APP_DEVELOPMENT.md`](docs/APP_DEVELOPMENT.md)).

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                                 Tang Nano 9K FPGA                                 |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                     Unified Dual-ISA Core (unified_cpu)                     |  |
|  |                                                                             |  |
|  |   +-----------------------+     +-------------------+     +-------------+   |  |
|  |   |  Auto-Mode Detector   | --> |  Multi-Cycle FSM  | --> | Shared ALU  |   |  |
|  |   | (RV32I vs Hack 16-bit)|     | (FETCH->EXEC->MEM)|     | (32-bit ops)|   |  |
|  |   +-----------------------+     +-------------------+     +-------------+   |  |
|  |               |                                                  |          |  |
|  |   +-----------------------+                         +-------------------+   |  |
|  |   | Hack uOp Translator   |                         | 32 x 32b Regfile  |   |  |
|  |   | (A, D, M, JMP decode) |                         | (Distributed RAM) |   |  |
|  |   +-----------------------+                         +-------------------+   |  |
|  +-----------------------------------------------------------------------------+  |
|                                        |                                          |
|                                  MMIO Interconnect                                |
|    +-------------------+-------------------+------------------+-----------------+ |
|    |                   |                   |                  |                 | |
| +-------+         +---------+         +---------+        +---------+       +----+ |
| | 16KB  |         |  8KB    |         | UART    |        | Timer   |       | SD | |
| | I-RAM |         |  D-RAM  |         | 115200  |        | 64-bit  |       | SPI| |
| +-------+         +---------+         +---------+        +---------+       +----+ |
+----+-------------------+-------------------+------------------+-----------------+-+
     |                   |                   |                  |                 |
 [Reset/Boot]        [Stack/Data]        [USB-UART]         [mtime/cmp]      [MicroSD]
```

### Clock

The SoC runs at **18 MHz** (`soc_pkg::CLK_HZ`): `board_top` feeds the board's 27 MHz
crystal through the GW1NR-9's rPLL (27 x 2 / 3) and holds the SoC in reset until the
PLL locks. Everything clock-derived follows from it: UART 156 clocks per bit, SD SPI
391 kHz during card init and 3 MHz after it, `mtime` 18 ticks per microsecond (the firmware's delays, the
Zephyr board's timebase), and the emulator's cycle timing. Simulations drive
`soc_top` at 18 MHz directly; only synthesis goes through `board_top`.

Why not 27 MHz: nextpnr's STA passed every placement tried at 27 MHz (post-route Fmax
33-39 MHz), yet all of them failed on the board: wrong `add`/`sub`/shift results, or
no output at all. Placed and routed once and then only the PLL divider changed (identical
placement and routing), the same five placements passed a CPU self-test at 24, 20.25 and
18 MHz and failed at 27 MHz. The open-source timing model is therefore optimistic
here by more than a third, so `make sta` checks the 18 MHz clock against a 27 MHz
target (`STA_FREQ` in the `Makefile`, 1.5x). A passing STA is still not proof that a
bitstream works; test it on the board.

---

## Lineage & Original Projects

The RTL modules in this repository were originally authored in VHDL-2008 and have been fully ported to modern Veryl:

- **CPU Core (`unified_cpu`)**: Ported from [`takayuki-nagata/hack_cpu`](https://github.com/takayuki-nagata/hack_cpu)
  - Unified datapath supporting both **RISC-V RV32I** (32-bit) and **Nand2Tetris Hack** (16-bit) execution with dynamic ISA auto-detection.
- **UART Controller (`uart_controller`)**: Ported from [`takayuki-nagata/uart_controller`](https://github.com/takayuki-nagata/uart_controller)
  - Parameterized baud rate clock timer (18.0 MHz -> 115200 bps), 8N1 serial framing, and dual 32-entry synchronous TX/RX FIFOs.
- **Arbitrary-Precision Math Engine & REPL (`vendor/bc_clone_rs`)**: Submodule from [`takayuki-nagata/bc_clone_rs`](https://github.com/takayuki-nagata/bc_clone_rs)
  - Embedded `bc_core` arbitrary-precision arithmetic engine with 10/10 math test suite running atop Zephyr RTOS. At about 260 KB it runs only on the emulator's `extended` profile (`make build-zephyr`), **not on the board**.
- **Hack Toolchain & C Firmware (`hack_demo`)**: Powered by [`takayuki-nagata/hack_tools`](https://github.com/takayuki-nagata/hack_tools) (`has` assembler & `m2h` transpiler)
  - 16-bit C and Hack assembly firmware testing recursive arithmetic, Fibonacci, array manipulations, and MMIO UART output.

---

## Memory & MMIO Register Map

### System Address Space
| Address Range | Size | Component | Description |
|:---|:---|:---|:---|
| `0x0000_0000` - `0x0000_3FFF` | 16 KB | **Instruction RAM** | Hardware `RESET_VECTOR` (`0x0000_0000`); the bitstream preloads it with the first stage (the boot ROM, see [Two-stage boot](#two-stage-boot-the-first-stage-and-the-sd-card)), and power-on and every soft reset start it. Writable: a program loaded here replaces the first stage until the FPGA is reconfigured |
| `0x2000_0000` - `0x2000_1FFF` | 8 KB | **Data RAM** | `.data`, `.bss`, stack, and heap. The first stage doesn't use it |
| `0x4000_0000` - `0x4000_000F` | 16 B | **UART Controller** | 115200 bps 8N1, 32-byte TX and RX FIFOs. `0x0` data: read pops a received byte, write sends one (only this offset transmits). `0x4` status: bit 0 `rx_empty`, bit 1 `tx_full`, bit 2 `overrun` (a byte was dropped, RX FIFO full), bit 3 `frame_err` (a stop bit was low); bits 2-3 stay set until the status register is read. While the RX FIFO holds data, the machine external interrupt is pending (`mip.MEIP`, level; taken when `mie.MEIE` and `mstatus.MIE` are set; reading the bytes clears it) |
| `0x4000_1000` - `0x4000_100F` | 16 B | **System Timer (CLINT)** | 64-bit `mtime` (`0x0`/`0x4`) and `mtimecmp` (`0x8`/`0xC`) registers |
| `0x4000_2000` - `0x4000_200F` | 16 B | **MicroSD SPI Master** | SPI TX/RX data (`0x0`), CS (`0x4`), busy (`0x8`), SCLK divider (`0xC`): half period in clocks, bits 7:0, resets to 23 (391 kHz, for card init); writes below 3 (3 MHz, the fastest the MISO synchronizer can read) set 3. The first stage initializes the card at 23 and switches to 3 when the card is ready. Writes while busy are ignored (all registers): wait for busy to clear first. Only power-on resets the registers, not a CPU soft reset |
| `0x4000_3000` - `0x4000_300F` | 16 B | **GPIO Controller** | 6 onboard active-low LEDs (`0x00`=ON, `0x3F`=OFF) & user buttons (Button S2 on pin 3); offset `0x8` is the ISA for the next soft reset (bit 8 valid, bit 0: 1 = RV32, 0 = Hack; valid clears once a soft reset has used it); offset `0xC` is a soft-reset trigger — writing `0x5A5A_A55A` (or its short form `0x0000_A55A`) pulses `cpu_soft_rst`, resetting the CPU FSM/PC/CSRs to `RESET_VECTOR` without a full FPGA reload |
| `0x8000_0000` - `0x807F_FFFF` | 8 MB | **PSRAM** | The on-package PSRAM (W955D8MBYA, two 4 MB dies) through `psram_controller` ([`vendor/psram_controller`](vendor/psram_controller)): code and data (RV32 only). Every access waits: a fetch or load takes 35 cycles more than from I-RAM/D-RAM, a store 34 more. The 8 MB repeat every 8 MB through `0x8FFF_FFFF`; the PSRAM's own registers (ID/configuration) are not reachable. Contents are undefined at power-up: write before reading. Its power-up sequence ends inside the power-on reset, so it is ready from the first instruction |

**Address decoding and aliases.** This describes how the hardware behaves, not an interface: applications use only the documented addresses and registers (the tables in this README; [Hardware revisions and compatibility](docs/APP_DEVELOPMENT.md#6-hardware-revisions-and-compatibility)). Only the address bits needed to tell the regions apart are decoded, so every region repeats and nothing raises an access fault:
- `0x2xxx_xxxx` is D-RAM repeating every 8 KB (`0x2000_2100` is the same word as `0x2000_0100`).
- Data reads of `0x0000_0000`-`0x0000_FFFF` return I-RAM, repeating every 16 KB; data writes there go to I-RAM. Other `0x0xxx_xxxx` addresses read D-RAM aliases and ignore writes.
- In `0x4xxx_xxxx`, bits `[15:12]` pick the peripheral and bits `[3:0]` the register; the rest are ignored.
- `0x8xxx_xxxx` is the PSRAM repeating every 8 MB (`0x8080_0020` is the same word as `0x8000_0020`). Instructions are fetched from it too; a fetch from any other address outside I-RAM returns the I-RAM word of its low address bits.
- Anything else reads as 0 and ignores writes.

**Writing code into I-RAM.** Stores to I-RAM (`0x0000_0000`-`0x0000_3FFF`) are how a loader places a program there. A store to the word holding the *very next* instruction is not supported: the store and that instruction's fetch hit the same block-RAM word in the same cycle, and the fetched word is undefined (the synthesis flow drops collision handling, see `SYNTH_GOWIN_OPTS` in the `Makefile`). Copy code into a region you are not executing from, and execute `fence.i` before jumping to it, as RISC-V requires anyway.

### RV32 CSRs
The CPU is machine-mode only. Accessing any CSR not listed here raises an illegal-instruction exception (`mcause` 2), as does writing a read-only one:

| CSRs | Notes |
|:---|:---|
| `mstatus` (`0x300`), `mie` (`0x304`), `mtvec` (`0x305`), `mscratch` (`0x340`), `mepc` (`0x341`), `mcause` (`0x342`), `mtval` (`0x343`) | `mstatus` keeps only MIE/MPIE (MPP reads as M); `mie` only MSIE/MTIE/MEIE; `mtvec` is direct mode only |
| `misa` (`0x301`), `mip` (`0x344`) | Read-only in practice: writes are ignored |
| `mcycle`/`mcycleh`, `minstret`/`minstreth` (`0xB00`/`0xB80`/`0xB02`/`0xB82`) | 64-bit, writable; `minstret` counts RV32 instructions that complete without trapping |
| `cycle`, `time`, `instret` and their `h` halves (`0xC00`-`0xC02`, `0xC80`-`0xC82`) | Read-only; `time` is the timer's `mtime` |
| `mvendorid`, `marchid`, `mimpid`, `mhartid`, `mconfigptr` (`0xF11`-`0xF15`), `mstatush` (`0x310`), `tselect`/`tdata1`-`tdata3` (`0x7A0`-`0x7A3`) | Read 0 (no debug triggers); writes to `mstatush` and the trigger registers are ignored |
| `mcountinhibit` (`0x320`), `mhpmevent3`-`31` (`0x323`-`0x33F`), `mhpmcounter3`-`31` and their `h` halves (`0xB03`-`0xB1F`, `0xB83`-`0xB9F`) | Read 0, writes ignored: no performance-monitoring events (the spec requires these CSRs to exist) |

Interrupts: timer (`MTI`, cause 7) from `mtime >= mtimecmp`, external (`MEI`, cause 11) while the UART RX FIFO holds data; `MSI` is never raised.

### Hack Mode Address Space (16-bit word addresses)
Hack instructions are packed two per 32-bit I-RAM word (first instruction in the low half, as `scripts/bin2hex.py` does). The data side:

| Address | Component | Notes |
|:---|:---|:---|
| `0x0000` - `0x5FFF` | **RAM** | Only 2K words exist (the D-RAM, one Hack word per 32-bit word): addresses repeat every `0x0800`, so `0x0900` is the same word as `0x0100` |
| `0x6000` | **UART data** | Read: received byte (pops the RX FIFO). Write: byte to send |
| `0x6001` - `0x6003` | **UART status** | Same bits as the RV32 status register; reading it clears `overrun`/`frame_err` |
| `0x6004` - `0x600F` | **GPIO** | Register = address bits `[3:0]`: `0x6004` button (1 = pressed), `0x6008` ISA for the next soft reset, `0x600C` soft reset in progress (read) and trigger (write). The LED register (GPIO `0x0`) is unreachable from Hack mode. To soft-reset, store the short key `0x0000_A55A`: an `@n` holds at most `0x7FFF`, but Hack registers are 32 bits wide in this CPU, so `0x255A + 0x4000 + 0x4000` forms it (`!0x5AA5` gives `0xFFFF_A55A`, which is ignored) |
| other | unmapped | Reads 0, writes ignored |

**ISA selection.** A loader that soft-resets into a program it placed in I-RAM gives that program's ISA in GPIO register `0x8` before the soft reset, and the CPU latches it. Only when no mode is given (power-on with a program already in I-RAM, or a soft reset without one) is the mode guessed from the first non-zero instruction word (`auto_mode_detector`): RV32I if its low 7 bits are an RV32I opcode; Hack if its low half is a C-instruction or a non-zero `@n`; otherwise the CPU stays in its reset default, RV32I, and keeps looking. So such a Hack program must not start with `@0`, nor with an `@n` whose `n & 0x7F` is an RV32I opcode (`0x03`, `0x13`, `0x17`, `0x23`, `0x33`, `0x37`, `0x63`, `0x67`, `0x6F`, `0x73`; e.g. `@19` or `@51`), which is detected as RV32I.

### Two-stage boot: the first stage and the SD card

The **first stage** (`firmware/stage1`, at most 16 KB) is in the bitstream, preloaded into
I-RAM; a board gets a new one only with a new bitstream. At power-on and at every soft
reset it prints its banner and then, unless Button S2 is held or a key arrives within
100 ms, loads `\VUX9K\STAGE2.BIN` from the card's first partition (FAT32; 8.3 names) into
the PSRAM, checks its header and CRC, and starts it with `a0` = 1 on an SDHC card (else 0),
`a1` = the FAT32 partition's first LBA (0 if none was mounted), `a2` = the first stage's
version. When `STAGE2.BIN` is missing or broken it starts `STAGE2.OLD`, the previous one.
Its data and stack are in the PSRAM below the second stage (`0x806F_0000`-`0x806F_FFFF`).

The **second stage** is a VUX9 v4 file of RV32 code that loads in the top 1 MB of the
PSRAM (`0x8070_0000`-`0x807F_FFFF`). Until the file manager and launcher replace it,
`firmware/stage2` is a provisional one: it shows what it was handed and installs a second
stage it receives over the UART (`fat::stage2::install`: the new file is written as
`STAGE2.NEW` and read back, the current `.BIN` becomes `.OLD`, then `.NEW` becomes `.BIN`, so
one of them stays bootable whenever power fails). The first stage writes nothing to the
card.

**The card**: FAT32 (an SDXC card formatted exFAT, or an SDSC card formatted FAT16, must be
reformatted as FAT32), with the directory `VUX9K` in its root and the second stage as
`VUX9K\STAGE2.BIN` (a PC can copy it there; a release ships it as `sd/VUX9K/STAGE2.BIN`).

#### VUX9 file header (64 bytes, v4)
A VUX9 file is a 64-byte header (`struct.pack("<IHHIIIIII32s", ...)`, little-endian)
followed by the payload, without padding:

| Offset | Field | Type | Value / Meaning |
|:---|:---|:---|:---|
| `0x00` - `0x03` | Magic Number | `u32` | `0x56555839` (`"VUX9"` ASCII) |
| `0x04` - `0x05` | Header Version | `u16` | `4`; any other value is rejected |
| `0x06` - `0x07` | Reserved | `u16` | `0`; any other value is rejected |
| `0x08` - `0x0B` | ISA Mode | `u32` | `0` = Nand2Tetris Hack 16-bit, `1` = RISC-V RV32I (a second stage must be `1`) |
| `0x0C` - `0x0F` | Payload Length | `u32` | Bytes after the header; the file is exactly 64 + this |
| `0x10` - `0x13` | Load Address | `u32` | Where the payload goes; for a second stage inside `0x8070_0000`-`0x807F_FFFF` |
| `0x14` - `0x17` | Entry Point | `u32` | RV32: word-aligned, inside the loaded payload |
| `0x18` - `0x1B` | Payload CRC32 | `u32` | IEEE 802.3 CRC32 of the payload |
| `0x1C` - `0x1F` | Version | `u32` | The file's version (the first stage shows it) |
| `0x20` - `0x3F` | Name | `32 bytes` | NUL-padded ASCII |

`python3 tools/vux_tool.py mkv4 IN OUT --load-addr ADDR` wraps a binary in one.

---

## Host Tooling & the First Stage

**Host setup.** If ModemManager runs on the host (most desktop distributions start it),
install [`tools/70-vux9k-board.rules`](tools/70-vux9k-board.rules) as its comment shows:
otherwise ModemManager probes the board's JTAG port after every bitstream load, and the
board's USB bridge applies the probe's 57600 baud to the board's serial port too, so the
next session reads noise. Give the board its own USB port or hub: another full-speed device
busy on the same hub makes the bridge drop output. Both are described in
[`docs/APP_DEVELOPMENT.md`](docs/APP_DEVELOPMENT.md) (sections 1 and 5).

### 1. Host Utility: `vux_tool.py`

[`tools/vux_tool.py`](tools/vux_tool.py) provides host-side management over UART (supports `--port` e.g. `/dev/ttyUSB3`, `socket://localhost:4000` for the emulator, or `auto`; it warns when ModemManager can probe the board):

```bash
# 1. The card as the first stage sees it: SDHC/SDSC, the FAT32 volume, STAGE2.BIN/.OLD checked
python3 tools/vux_tool.py sd-info

# 2. Install a second stage as \VUX9K\STAGE2.BIN (it is RAM-booted first, then installs
#    itself; the previous one becomes STAGE2.OLD), and boot it
python3 tools/vux_tool.py install-stage2 build/firmware/stage2/STAGE2.BIN

# 3. RAM-boot a second stage without writing the card (development, recovery)
python3 tools/vux_tool.py run build/firmware/stage2/STAGE2.BIN

# 4. Wrap a binary in a VUX9 v4 header
python3 tools/vux_tool.py mkv4 build/firmware/stage2/stage2.bin STAGE2.BIN --load-addr 0x80700000

# 5. Build a FAT32 card image, for the emulator (--sd), without a board
python3 tools/vux_tool.py mkfat sd.img --file VUX9K/STAGE2.BIN=build/firmware/stage2/STAGE2.BIN

# 6. Interactive Serial Monitor (115200 bps)
python3 tools/vux_tool.py monitor

# 7. Trigger a hardware FPGA reset (openFPGALoader --reset)
python3 tools/vux_tool.py reset
```

### 2. The first stage's prompt

Hold Button S2 while the board starts (or resets), or press a key within 100 ms of the
banner (`vux_tool.py` sends keys until the prompt answers), and the first stage stays at
its `s1> ` prompt (115200 bps 8N1):

| Command | Key | Description |
|:---|:---|:---|
| Help | `h` or `?` | The command list |
| Boot | `b` | Load and start `\VUX9K\STAGE2.BIN` (`STAGE2.OLD` if it is broken), as at power-on |
| RAM boot | `u` | Receive a second stage over the UART (transfer v2: `vux_tool.send_upload_v2`) into its window and start it, without writing the card |
| SD info | `i` | Initialize the card again; SDHC/SDSC, the FAT32 volume, and both files checked as a boot would (version, name, size, CRC) |
| Soft reset | `r` | Start over (and boot, unless a key or S2 stops it again) |

What keeps a second stage from starting is one `[BOOT] ...` line (e.g. `STAGE2.BIN: CRC
mismatch; STAGE2.OLD: not found`), and the prompt follows. An exception in the first stage
(it enables no interrupts) prints `[TRAP] mcause=0x… mepc=0x… mtval=0x…` and stops with
LEDs 1/3/5 and 0/2/4 blinking in turn; reset the board.

---

## Emulator (`vux9k-emu`)

`emu/` is a cycle-accurate emulator of the SoC in Rust: both ISAs, the CSRs and traps,
the memories with the real address aliasing, and every peripheral down to the UART's
bit timing and the SD SPI master's transfer time, with a byte-level SD card. It is
checked instruction by instruction against the RTL (`make sim-lockstep`: every fetch
cycle, register write, bus write, trap and UART byte must match), and runs the
riscv-tests with the same results as the RTL (`make test-isa-emu`).

```bash
make emu                                   # build/emu/target/release/vux9k-emu
# Power on like the board (firmware.hex preload) with a FAT32 card image, UART on this terminal
python3 tools/vux_tool.py mkfat sd.img --file VUX9K/STAGE2.BIN=build/firmware/stage2/STAGE2.BIN
build/emu/target/release/vux9k-emu --sd sd.img --stdio
# ... or on TCP, for vux_tool.py (--port socket://localhost:4000) or a terminal program
build/emu/target/release/vux9k-emu --sd sd.img --sd-write-back --tcp 4000
# Start an application image in its ISA from I-RAM, without the first stage; stop when it
# prints a text
build/emu/target/release/vux9k-emu --no-firmware --load build/hack/firmware.bin --mode hack --until "(100%)!"
build/emu/target/release/vux9k-emu --no-firmware --load build/zephyr-demo/zephyr/zephyr.bin --mode riscv --stdio
```

Interactive modes run at the SoC's 18 MHz, so the firmware's timeouts behave as on
hardware; `--trace FILE` logs every instruction. Profiles: `real` (default: 16 KB
I-RAM, 8 KB D-RAM and the 8 MB PSRAM, as the board) and `extended`
(512 KB / 256 KB, same MMIO and PSRAM) —
**the extended profile is not real hardware**; it exists for programs too big for the
board, such as bc. Python tests drive the emulator through the `vux9k_emu` module
(`make emu-py`; helpers in `sim/emu/vux9k.py`).

`make emu-bench` (`scripts/emu_bench.py`) measures how fast that runs (millions of
instructions per second, driven from Python as the tests do) on a fixed RV32I loop, bc,
the Zephyr and Hack demos and the first stage, and prints a digest of each final state.
`--compare` checks the digests against a saved run of another build: a change made for
speed must not change any. CI prints the numbers but never fails on them; compare them on
one machine.

### Zephyr boards

`zephyr_workspace/boards/vux9k/` has two targets: `vux9k`, the real board (18 MHz, the
SoC timer as `andestech,machine-timer`, tickless at 1000 ticks/s, 14 KB of I-RAM and 8 KB of
D-RAM, which the link enforces; it moves to the PSRAM once the second stage loads applications), and `vux9k/vux9k/ext`,
the extended profile for the emulator only. `make build-zephyr-demo` builds the Rust demo
(`zephyr_workspace/app/`, Zephyr + a Rust staticlib) for the real board; `make
build-zephyr` builds bc_clone_rs for `ext`. `make build-zephyr-irq-echo` builds
`zephyr_workspace/irq_echo/`, an echo through the UART driver's interrupt-driven API
(RX on the machine external interrupt; see `zephyr_workspace/README.md`, "UART").

## Application Development & Releases

To write applications for the board without building the SoC, use a
[release](https://github.com/takayuki-nagata/VUX9K/releases): the bitstream, the second
stage for the card, the demos, `vux_tool.py`, the Zephyr board support package and the
emulator, with [`docs/APP_DEVELOPMENT.md`](docs/APP_DEVELOPMENT.md) as the guide. `make
dist` builds the same tree into `build/dist/` (`make check-dist` runs its demos on its
own emulator); CI uploads it for every push to `main`, and a release publishes one of
those after it has been tested on the board ([`docs/RELEASING.md`](docs/RELEASING.md)).

## Quickstart & Build Guide

### Prerequisites
The install steps of [`.github/workflows/ci.yml`](.github/workflows/ci.yml) are the
reference: they pin every tool version CI builds and tests with.

1. **Rust Toolchain**: [rustup](https://rustup.rs/); `firmware/rust-toolchain.toml` pins
   the firmware's toolchain with the `riscv32i-unknown-none-elf` target and `llvm-tools`
   (rustup installs it on first use).
   ```bash
   cargo install cargo-llvm-cov --version 0.9.1 --locked   # make coverage-rust
   ```
2. **OSS CAD Suite** (Yosys, nextpnr, Icarus Verilog, Verilator, eqy, mcy, openFPGALoader):
   [YosysHQ/oss-cad-suite-build](https://github.com/YosysHQ/oss-cad-suite-build). The
   `Makefile` looks for it in `~/.local/oss-cad-suite/bin` (`OSS_CAD_SUITE_BIN`).
3. **Veryl Compiler**:
   ```bash
   cargo install veryl --version 0.21.0 --locked
   ```
4. **Python Environment** ([uv](https://docs.astral.sh/uv/)): `make setup` creates `.venv`
   (Python 3.13), installs the test and lint packages, and points Git at `.githooks/`
   (the pre-commit hook runs `make check`).
5. **Hack Toolchain** (`has`, `m2h`, msp430-gcc): `make install-hack-tools`, after
   `make setup` (it installs `m2h` into `.venv`). It puts `has` and `msp430-gcc` in
   `~/.local/bin`, which must be on your `PATH`.
6. **Zephyr** (`make build-zephyr-demo` and the Zephyr tests): Zephyr v3.7.2 and Zephyr
   SDK 0.16.8 with the `riscv64-zephyr-elf` toolchain, per Zephyr's Getting Started Guide,
   plus Zephyr's `scripts/requirements-base.txt` in `.venv`, and the host packages CI
   installs (`device-tree-compiler`, `ninja-build`, `gcc-riscv64-linux-gnu` on Debian/Ubuntu). The `Makefile` looks for them
   in `~/zephyrproject/zephyr` and `~/.local/zephyr-sdk-0.16.8`; set `ZEPHYR_BASE` and
   `ZEPHYR_SDK_INSTALL_DIR` for other places.

### Building & Flashing

```bash
# 1. Build the Rust boot firmware (first stage; second stage as build/firmware/stage2/STAGE2.BIN)
make firmware

# 2. Synthesize Veryl SoC RTL -> GW1NR-9 Bitstream (build/synth/pack.fs)
make build-hw

# 3. Flash Bitstream to Tang Nano 9K SRAM (fast volatile load)
make prog-sram

# 4. Flash Bitstream to Tang Nano 9K On-Board SPI Flash (persistent)
make prog-flash
```

---

## Verification & Test Targets

The project provides a comprehensive, multi-tiered verification framework spanning RTL simulation, Gate-Level Simulation (GLS) with Gowin primitive cells (`cells_sim.v`), Static Timing Analysis (STA) with Nextpnr, and automated physical hardware testing:

```bash
# 1. Full CI Verification Suite (All Simulation Suites + Strict Static Timing Analysis STA)
make test-ci
# or make test

# 2. Simulation-Only Verification Suite (RTL, Arch Compliance & GLS Netlists)
make test-sim

# 3. Static Timing Analysis (STA): 27.0 MHz target for the 18 MHz SoC clock (1.5x, see "Clock"), strict check
make sta

# 4. End-to-End Virtual Hardware Simulation Flow (RTL & GLS on-demand)
make sim-hw-flow
make sim-gls-hw-flow

# 5. Hardware Target: Synthesize, Flash SRAM & Run Automated Real-Board Test Suite on Tang Nano 9K
make test-hw

# 6. Hardware Target: CPU self-test on every routed seed's placement (after `make timing`)
make hw-smoke

# 7. Static checks (seconds; the pre-commit hook runs `make check`)
make check check-rtl-syntax lint-rtl

# 8. Nightly tier: the long flows, new random seeds
make test-slow

# 9. Long runs, one at a time: RTL refactor proof, timing over several PnR seeds,
#    RTL/firmware coverage, mutation testing
make eqy
make timing
make coverage
make mutation
```

Which tier runs when, and what each needs, is in [`AGENTS.md`](AGENTS.md) ("Test tiers").

### Test Suite Architecture

Short tests run on Icarus, long SoC/GLS runs on Verilator (`SIM_UNIT` / `SIM_SOC` in the `Makefile`; Verilator needs `perl`). `make test-sim` (CI on every push/PR) runs everything below except the rows marked *slow*, which `make test-slow` (CI nightly / manual) adds.

| Level | Command | Typical Time | Verification Scope |
|:---|:---|:---|:---|
| **RTL Unit Tests** | `make sim-unit` | ~1 min | 29 cocotb modules: CPU (ALU, decoder, regfile, CSRs, LSU, next-PC, trap unit, mode detector, Hack translator and ops, RV32I smoke, branches, trap path, CSRs through the CPU, bus wait states), UART, timer, GPIO, SD SPI master, address decoder, RAM, the PSRAM window's bus bridge, and the SP/SDPB block-RAM cell models. Each has boundary-value and random tests against a Python model (fixed seed; *slow*: `sim-unit-random`, a new seed each day) |
| **PSRAM Word Model** | `make sim-psram-contract` | ~1 min | `sim/psram_word_model.veryl` (the PSRAM controller without pins, for Verilator SoC runs) side by side with the real controller on the pin-level W955D8MBYA model (Icarus only): same power-up length (`soc_pkg::PSRAM_INIT_CYCLES`), same response cycle and read data for random requests, no protocol/timing error in the PSRAM model |
| **ISA Tests** | `make test-isa` | ~20 sec | riscv-tests `rv32ui`/`rv32mi` on `tb_hex_runner`, whose RAM has the SoC's synchronous timing (56 pass; 2 known gaps, hardware misaligned access and PMP, tracked as expected failures in `scripts/run_riscv_tests.py`) |
| **GLS ISA Tests** | `make test-isa-gls` | ~1.5 min | The same riscv-tests on the gate-level `unified_cpu` netlist |
| **ACT4 Tests** | `make test-act4` (`-gls`, `-emu`) | ~2 min each | riscv-arch-test 4.1.0: self-checking tests whose expected values the Sail reference model computed for this CPU's configuration (`scripts/act4/`), on RTL, netlist and emulator (73 pass; 4 known differences from the Sail configuration, tracked in `scripts/run_riscv_tests.py`). `make act4-elfs` generates them with the upstream image (docker or podman) |
| **ISA Tests with Waits** | `make test-isa-waits` | ~4 min | riscv-tests and ACT4 on `tb_hex_runner` with pseudo-random wait cycles on the instruction and data buses (the CPU's stall logic) |
| **GLS Unit Tests** | `make sim-gls-unit` | ~20 sec | Gowin primitive netlists (`sim/gowin_cells_sim.veryl`) of the CPU, UART controller, mode detector and the PSRAM window's bus bridge |
| **SoC Boot** | `make sim-soc-fast` / `sim-soc-fast-icarus` | ~10 sec / a few min | Power-on reset, the first stage booting `\VUX9K\STAGE2.BIN` from a FAT32 card into the PSRAM and running it there (Verilator / 4-state Icarus with the real PSRAM controller) |
| **SoC MMIO** | `make sim-soc-mmio` | ~5 sec | RV32I program from I-RAM checking the memory map, D-RAM lanes, the PSRAM window (both dies' byte lanes, its 8 MB aliases, the hidden register space, code run from it), timer, GPIO, UART RX and status flags, soft reset; timer and UART RX interrupts. Verilator runs it on `sim/psram_word_model.veryl` in place of the PSRAM controller; 4-state Icarus SoC runs have the real controller on the IP's pin-level PSRAM model |
| **First Stage Prompt** | `make sim-boot` | ~40 sec | A key keeps the first stage at its prompt; its commands' output; a soft reset boots the card |
| **Hack Mode** | `make sim-hack-rtl` | ~2 sec | Hack C firmware's self-test report over the UART |
| **SD Edge Cases** | `make sim-sd-quirks` | ~20 sec | Strict SD model: power-up preamble, an SDSC card booted twice across a soft reset |
| **End-to-End Flow** | `make sim-hw-flow` | ~1 min | RAM boot of the second stage over transfer v2, which installs a `STAGE2.BIN` on the FAT32 card; the first stage boots it after a soft reset |
| **GLS SoC Boot** | `make sim-soc-gls-fast` | ~1.5 min | Full Gowin netlist boots to the first stage's prompt (S2 held) |
| *slow* **GLS End-to-End** | `make sim-gls-hw-flow` | ~3-6 min | The end-to-end flow on the full Gowin netlist |
| *slow* **Icarus End-to-End** | `make sim-hw-flow-icarus` | ~30 min | The end-to-end flow on 4-state Icarus |
| *slow* **PSRAM on Icarus** | `make sim-psram-icarus` | a few min | The SoC MMIO program's PSRAM window checks with the real controller on the pin-level W955D8MBYA model (no protocol/timing error in it), and the PSRAM word model's contract test against the controller's Gowin netlist |
| **Emulator** | `make emu-test` / `test-isa-emu` | ~10 sec | Rust unit tests of the emulator; riscv-tests on it (same results as `test-isa`) |
| **Emulator Speed** | `make emu-bench` | ~10 sec | MIPS from Python and final-state digests (`--compare` with a saved run); prints only |
| **Firmware on the Emulator** | `make test-emu` | ~30 sec | The first stage on FAT32 cards (boot, `.OLD`, what keeps a second stage from starting, card types, prompt, soft reset), RAM boot and install through `vux_tool.py`, Hack demo, Zephyr demo and interrupt-driven UART echo from I-RAM, bc (extended profile), SD transcripts, FAT32 images from mkfs.fat/mtools |
| **Firmware Host Tests** | `make test-fw-host` / `firmware-size` | ~5 sec | `firmware/fw_common` on the host (FAT32, v4 header, transfer v2, RAM boot); first stage ≤ 16 KB |
| **Firmware Coverage** | `make coverage-fw` | ~40 sec | Source-line coverage of the firmware and demos from the emulator tests, against `coverage/thresholds.toml` (report in `build/coverage/fw/`) |
| **Rust Coverage** | `make coverage-rust` | a few min | Line coverage of the emulator (its cargo tests and the Python-driven emulator runs) and `fw_common` (host tests) with cargo-llvm-cov, against `coverage/thresholds.toml` (report in `build/coverage/rust/`) |
| **RTL ↔ Emulator Lockstep** | `make sim-lockstep` | ~40 sec | Random RV32I, traps/interrupts, the PSRAM window, Hack demo and the first stage booting a card on RTL and emulator, compared cycle by cycle (*slow*: `sim-lockstep-slow`, more programs) |
| **Zephyr Demo** | `make sim-zephyr-demo-rtl` | ~45 sec | The Zephyr Rust demo preloaded into I-RAM on the RTL |
| **Zephyr UART Interrupts** | `make sim-zephyr-irq-echo-rtl` | ~1 min | The interrupt-driven UART echo preloaded into I-RAM on the RTL: a 64-byte line, longer than both FIFOs, comes back with no overrun |
| **Static Timing (STA)**| `make sta` | ~15 sec | Exhaustive post-PnR timing analysis, Fmax verification, and critical path breakdown (`build/synth/soc_sta.json`) |
| **Real Hardware** | `make test-hw` | ~1 min | The two-stage boot on the Tang Nano 9K with a FAT32 card via `scripts/test_hardware.py` (9 tests) |
| **Real Hardware, Release Tree** | `make test-hw-dist DIST=<dir>` | ~1 min | The same 9 tests with a `make dist` tree's bitstream and second stage, nothing rebuilt: how a release candidate is tested ([`docs/RELEASING.md`](docs/RELEASING.md)) |

### Real Hardware Test Suite (9 Automated Checks)
`scripts/test_hardware.py` needs a FAT32 card with the directory `VUX9K` in its root; it
writes only there, and leaves the second stage under test installed:
1. **First stage prompt and banner** (`vux_tool.enter_s1`, then a soft reset: the banner with the expected version)
2. **SD card and FAT32 volume** (`i`: card type and the partition's LBA)
3. **RAM boot of the second stage** (`u` / `vux_tool.ram_boot`: `a0`-`a2` as expected)
4. **RAM boot refusals** (bad magic, bad CRC, a load address outside the window: refused, still at the prompt)
5. **Install STAGE2.BIN twice** (versions 9001 and 9002, through the second stage's `n`)
6. **Soft reset to the first stage**, which boots version 9002
7. **The card as the first stage sees it** (`i`: `.BIN` 9002 and `.OLD` 9001)
8. **A broken STAGE2.BIN** (written unchecked with the second stage's `x`): the first stage boots `STAGE2.OLD`
9. **Install the second stage under test** and boot it

> [!NOTE]
> Before the tests, it warns when other full/low-speed USB devices share the board's USB hub, which makes the board's USB-UART bridge drop output, and it refuses to start while ModemManager could probe the board (see "Host setup" above and `docs/APP_DEVELOPMENT.md`). It runs against the emulator as well: `vux9k-emu --tcp PORT --sd <mkfat image>` and `--port socket://127.0.0.1:PORT`.

### Board Smoke Test of Routed Seeds (`make hw-smoke`)

A passing STA does not prove a bitstream works (see "Clock"), so check the placement
itself on the board before adopting a seed. `make timing` routes every seed into
`build/synth/pnr/seed_<N>/`; `make hw-smoke` (`scripts/hw_smoke.py`) then, for each
seed, replaces only the I-RAM block RAMs' initial contents with `firmware/hw_test`,
packs, loads the result into SRAM and reads the verdict. The placement and routing
stay exactly those of the seed; the SD card and the boot chain are not involved.

`hw_test` runs eleven instruction-pattern checksums (add, sub, logic, slt, shifts,
branches, dependent sub/branch loops, back-to-back dependencies, loads/stores of every
width, all registers) and four on the PSRAM (its 21 address lines walking one and zero,
the hidden register space, the first and last 2 KB written and read back twice, its 8 MB
aliases, byte/halfword lanes, code copied there and run, the cycles each access waits) against the values the emulator computes, one line each, then
`RESULT PASS|FAIL <passed>/<total> <failure mask>`, repeated every 2 s: the host's
USB-UART bridge drops output when another full-speed device shares its USB hub
(see `docs/APP_DEVELOPMENT.md`), and the repeated line alone carries the verdict. Its LEDs: 0x01 from the first instruction, the test number while
it runs, 0x15/0x2A alternating on PASS, the first failing test number blinking on
FAIL, 0x2A steady on a trap. `sim/emu/test_hw_test.py` keeps it passing on the
emulator.

---

## Directory Structure

```
VUX9K/
├── Makefile                        # Unified build, test, synthesis, and flash automation
├── Veryl.toml                      # Veryl project configuration & dependencies
├── tangnano9k.cst                  # Physical pin constraints for Tang Nano 9K (GW1NR-9)
├── soc/                            # Veryl SoC Top Level & Interconnect
│   ├── cpu/                        # Unified CPU & Dual-ISA modules
│   │   ├── auto_mode_detector.veryl    # First-instruction ISA auto-detector
│   │   ├── hack_translator.veryl       # Hack 16-bit instruction to uOp translator
│   │   ├── rv32i_alu.veryl             # Shared 32-bit ALU
│   │   ├── rv32i_csrs.veryl            # Machine-mode CSRs (mstatus, mie, mtvec, mepc, mcause, ...) and the cycle/time/instret counters
│   │   ├── rv32i_decode.veryl          # RV32I instruction decoder & immediate generator
│   │   ├── rv32i_pkg.veryl             # Common type definitions and opcodes
│   │   ├── rv32i_regfile.veryl         # Dual-port 32 x 32-bit register file (Distributed RAM)
│   │   ├── rv32i_lsu.veryl             # Load/store byte-lane alignment (sub-word loads/stores)
│   │   ├── rv32i_trap_unit.veryl       # Exception/interrupt decision (illegal, misaligned, ECALL/EBREAK, MRET)
│   │   ├── next_pc_unit.veryl          # Next-PC selection with the RV32I branch / Hack jump conditions
│   │   └── unified_cpu.veryl           # Multi-cycle Dual-ISA CPU Top Module (FETCH, EXECUTE, MEM_WAIT)
│   ├── uart/                       # UART Controller & FIFOs
│   │   ├── clk_timer.veryl             # Parameterized baud rate clock timer
│   │   ├── fifo_sync.veryl             # 32-entry synchronous FIFO buffer
│   │   ├── shift_registers.veryl       # Parallel load shift register
│   │   ├── uart_tx.veryl               # 8N1 serial transmitter
│   │   ├── uart_rx.veryl               # 8N1 serial receiver
│   │   └── uart_controller.veryl       # Integrated UART subsystem
│   ├── gpio_controller.veryl       # LED & button GPIO controller, incl. CPU soft-reset trigger
│   ├── sdcard_spi.veryl            # MicroSD SPI Master controller
│   ├── timer_core.veryl            # 64-bit mtime/mtimecmp timer core
│   ├── soc_ram.veryl               # Harvard 16KB I-RAM + 8KB D-RAM memory
│   ├── soc_pkg.veryl               # SoC constants: clock-derived dividers, data-address map
│   ├── soc_addr_decoder.veryl      # Data-address decoder (one instance per read/write address)
│   ├── soc_xmem_bridge.veryl       # CPU side of the PSRAM window: fetch/load/store -> req/rsp, CPU ready
│   └── soc_top.veryl               # Tang Nano 9K SoC top-level wrapper
├── firmware/                       # Cargo workspace: bare-metal Rust boot firmware
│   ├── Cargo.toml                  # Workspace manifest (members: stage1, stage2, fw_common, fw_drivers, hw_test)
│   ├── stage1/                         # The first stage: the boot ROM in I-RAM (bootstrap/: start.s, link.x)
│   ├── stage2/                         # The provisional second stage (\VUX9K\STAGE2.BIN, runs in the PSRAM)
│   ├── fw_common/                      # Firmware logic without MMIO (FAT32, v4 header, transfer v2), tested on the host
│   ├── fw_drivers/                     # MMIO drivers the stages share (UART, GPIO, mtime, MicroSD SPI)
│   └── hw_test/                        # Board self-test for make hw-smoke (replaces the first stage in BRAM)
├── hack_demo/                      # Hack 16-bit C/Assembly demo app (toolchain self-test)
├── zephyr_workspace/               # Zephyr module: vux9k boards, SoC, UART driver, dts (Apache-2.0, own README)
│   ├── app/                        # Zephyr Rust demo for the real board (rust_demo staticlib)
│   └── irq_echo/                   # Zephyr interrupt-driven UART echo (C) for the real board
├── docs/                           # APP_DEVELOPMENT.md (application developers), RELEASING.md, dist-README.md
│   └── agents/                     # Topic guides for AI agents (indexed by AGENTS.md)
├── emu/                            # Rust emulator: core, CLI (vux9k-emu), Python module (vux9k_emu)
├── sim/                            # cocotb & pytest testbenches (run via sim/runners/test_sim.py)
│   ├── unit/                       # cocotb unit tests, one RTL module each
│   ├── integration/                # SoC-level cocotb tests, SD card and serial models
│   ├── runners/                    # pytest entry point and the cocotb runner (sim_runner.py)
│   ├── emu/                        # pytest tests on the emulator (firmware, demos, lockstep compare)
│   └── sd_transcripts/             # SD card exchanges both card models must reproduce
├── coverage/                       # thresholds.toml: minimum coverage per image
├── scripts/                        # Build/CI plumbing (elf2bin.py, run_riscv_tests.py, test_hardware.py, run_pnr.py, report_sta.py, ...)
│   ├── riscv_tests/, act4/         # riscv-tests and riscv-arch-test (ACT4) configuration
│   └── mcy/                        # mutation testing (mcy) driver
├── tools/                          # End-user CLI: vux_tool.py (v4 files, FAT32 images, RAM boot, install, monitor); 70-vux9k-board.rules (udev, ModemManager)
├── vendor/                         # bc_clone_rs and psram_controller (git submodules); riscv-tests (fetched on demand by run_riscv_tests.py, gitignored)
└── build/                          # Unified build output (gitignored) - every generated artifact lands here
    ├── veryl/                      # veryl build --out-dir output: generated .sv/.map for soc/, soc/cpu/, soc/uart/, sim/
    ├── firmware/                   # firmware.bin/.hex, firmware_d0-3.hex (the first stage), stage2/STAGE2.BIN, hw_test/
    ├── hack/                       # Hack 16-bit firmware.bin/.hex
    ├── riscv_tests/                # riscv-tests ELFs/hex images; runs/<test>/ (program.hex, verdict.txt, sim.log)
    ├── zephyr/                     # Zephyr `west build` output
    ├── synth/                      # soc.json, soc_syn.v, soc_pnr.json, soc_sta.json, pack.fs, unit netlists
    ├── emu/                        # emulator build (cargo target) and the Python module
    ├── coverage/                   # coverage reports (fw/: firmware lines from the emulator; rust/: host Rust lines)
    ├── dist/                       # make dist: the release tree (docs/APP_DEVELOPMENT.md)
    └── sim/                        # cocotb builds (<sim>[-gls]/<toplevel>/) and per-test run dirs/results
```

---

## License

This project is licensed under the **[MIT License](LICENSE)**.
Zephyr RTOS Out-of-Tree components (`zephyr_workspace/`) are licensed under the **Apache License 2.0** ([`zephyr_workspace/LICENSE`](zephyr_workspace/LICENSE)).
`sim/gowin_cells_sim.veryl` (Gowin cell models for gate-level simulation) contains portions adapted from [Yosys](https://github.com/YosysHQ/yosys) and is licensed under **MIT AND ISC**; the Yosys notice is in the file header.
Most files include SPDX license headers.
