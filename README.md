# VUX9K

**VUX9K** (Veryl Unified eXecution on Tang Nano 9K) is an open-source **Dual-ISA (RISC-V RV32I & Nand2Tetris Hack 16-bit) System-on-Chip (SoC)** written 100% in **[Veryl](https://github.com/veryl-lang/veryl)** targeting the **Sipeed Tang Nano 9K** FPGA board (Gowin GW1NR-9).

The SoC features a multi-cycle Unified CPU core capable of seamlessly executing both standard **32-bit RISC-V RV32I** instructions and **16-bit Nand2Tetris Hack** machine code, integrated with a hardware MicroSD SPI master, full-duplex UART, 64-bit CLINT timer, GPIO, and an on-chip **Bare-Metal Rust Boot Manager** capable of loading and flashing multi-sector dual-ISA images from the MicroSD card (MBR gap).

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
  - Embedded `bc_core` arbitrary-precision arithmetic engine with 10/10 math test suite running atop Zephyr RTOS.
- **Hack Toolchain & C Firmware (`hack_demo`)**: Powered by [`takayuki-nagata/hack_tools`](https://github.com/takayuki-nagata/hack_tools) (`has` assembler & `m2h` transpiler)
  - 16-bit C and Hack assembly firmware testing recursive arithmetic, Fibonacci, array manipulations, and MMIO UART output.

---

## Memory & MMIO Register Map

### System Address Space
| Address Range | Size | Component | Description |
|:---|:---|:---|:---|
| `0x0000_0000` - `0x0000_37FF` | 14 KB | **Instruction RAM (Lower)** | Hardware `RESET_VECTOR` (`0x0000_0000`); preloaded with Rust Boot Manager (factory fallback), target region for SD slot application execution |
| `0x0000_3800` - `0x0000_3FFF` | 2 KB | **Resident Loader (Upper I-RAM)** | Immutable resident bootloader, entered only via a software jump to `0x0000_3800` (never by hardware reset); checks the mailbox, reads the requested SD slot once to verify it (CRC32) and again into I-RAM, then triggers a CPU soft-reset (see GPIO `0x4000_300C` below) to resume execution at `RESET_VECTOR` |
| `0x2000_0000` - `0x2000_1FFF` | 8 KB | **Data RAM** | `.data`, `.bss`, stack, and heap. The last 8 bytes (`0x2000_1FF8`-`0x2000_1FFF`) belong to the loaders: the mailbox is at `0x2000_1FFC` |
| `0x4000_0000` - `0x4000_000F` | 16 B | **UART Controller** | 115200 bps 8N1, 32-byte TX and RX FIFOs. `0x0` data: read pops a received byte, write sends one (only this offset transmits). `0x4` status: bit 0 `rx_empty`, bit 1 `tx_full`, bit 2 `overrun` (a byte was dropped, RX FIFO full), bit 3 `frame_err` (a stop bit was low); bits 2-3 stay set until the status register is read. While the RX FIFO holds data, the machine external interrupt is pending (`mip.MEIP`, level; taken when `mie.MEIE` and `mstatus.MIE` are set; reading the bytes clears it) |
| `0x4000_1000` - `0x4000_100F` | 16 B | **System Timer (CLINT)** | 64-bit `mtime` (`0x0`/`0x4`) and `mtimecmp` (`0x8`/`0xC`) registers |
| `0x4000_2000` - `0x4000_200F` | 16 B | **MicroSD SPI Master** | SPI TX/RX data (`0x0`), CS (`0x4`), busy (`0x8`), SCLK divider (`0xC`): half period in clocks, bits 7:0, resets to 23 (391 kHz, for card init); writes below 3 (3 MHz, the fastest the MISO synchronizer can read) set 3. The Boot Manager initializes the card at 23 and switches to 3 when the card is ready. Writes while busy are ignored (all registers): wait for busy to clear first. Only power-on resets the registers, not a CPU soft reset |
| `0x4000_3000` - `0x4000_300F` | 16 B | **GPIO Controller** | 6 onboard active-low LEDs (`0x00`=ON, `0x3F`=OFF) & user buttons (Button S2 on pin 3); offset `0x8` is the ISA for the next soft reset (bit 8 valid, bit 0: 1 = RV32, 0 = Hack; valid clears once a soft reset has used it); offset `0xC` is a soft-reset trigger — writing `0x5A5A_A55A` (or its short form `0x0000_A55A`) pulses `cpu_soft_rst`, resetting the CPU FSM/PC/CSRs to `RESET_VECTOR` without a full FPGA reload |

**Address decoding and aliases.** Only the address bits needed to tell the regions apart are decoded, so every region repeats and nothing raises an access fault:
- `0x2xxx_xxxx` is D-RAM repeating every 8 KB (`0x2000_2100` is the same word as `0x2000_0100`).
- Data reads of `0x0000_0000`-`0x0000_FFFF` return I-RAM, repeating every 16 KB; data writes there go to I-RAM. Other `0x0xxx_xxxx` addresses read D-RAM aliases and ignore writes.
- In `0x4xxx_xxxx`, bits `[15:12]` pick the peripheral and bits `[3:0]` the register; the rest are ignored.
- Anything else reads as 0 and ignores writes.

**Writing code into I-RAM.** Stores to I-RAM (`0x0000_0000`-`0x0000_3FFF`) are how the loaders place SD slot payloads. A store to the word holding the *very next* instruction is not supported: the store and that instruction's fetch hit the same block-RAM word in the same cycle, and the fetched word is undefined (the synthesis flow drops collision handling, see `SYNTH_GOWIN_OPTS` in the `Makefile`). Copy code into a region you are not executing from, and execute `fence.i` before jumping to it, as RISC-V requires anyway.

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

**ISA selection.** Programs launched from an SD slot run in the ISA their VUX9 header declares: the Resident Loader writes it to GPIO register `0x8` before the soft reset, and the CPU latches it. Only when no mode is given (power-on with a program already in I-RAM, or a soft reset without one) is the mode guessed from the first non-zero instruction word (`auto_mode_detector`): RV32I if its low 7 bits are an RV32I opcode; Hack if its low half is a C-instruction or a non-zero `@n`; otherwise the CPU stays in its reset default, RV32I, and keeps looking. So such a Hack program must not start with `@0`, nor with an `@n` whose `n & 0x7F` is an RV32I opcode (`0x03`, `0x13`, `0x17`, `0x23`, `0x33`, `0x37`, `0x63`, `0x67`, `0x6F`, `0x73`; e.g. `@19` or `@51`), which is detected as RV32I.

### MicroSD Card Sector Map (Multi-Slot MBR Gap Boot)
The MBR gap (`LBA 64` - `LBA 2047`, ~1 MB) is partitioned into 10 fixed 32 KB program slots (64 sectors per slot), keeping FAT32/exFAT filesystems intact:

| Slot | LBA Range | Byte Offset | Size | Allocation / Purpose | Filesystem Safety |
|:---|:---|:---|:---|:---|:---|
| - | `LBA 0` | `0x0000_0000` | 512 B | **MBR (Master Boot Record)** | Protected (Never modified) |
| - | `LBA 1` - `63` | `0x0000_0200` | 31.5 KB | Reserved Partition Headers | Protected |
| **0** | `LBA 64` - `127` | `0x0000_8000` | 32 KB | **Boot Manager SD Override** | Auto-booted by Resident Loader if valid |
| **1** | `LBA 128` - `191` | `0x0001_0000` | 32 KB | **User Application 1 (Default)** | **Launched via physical Button S2 or key `1`** |
| **2** | `LBA 192` - `255` | `0x0001_8000` | 32 KB | **User Application 2** | Launched via key `2` |
| **3** - **8**| `LBA 256` - `639` | `0x0002_0000` | 192 KB | **User Applications 3 to 8** | Launched via keys `3` to `8` |
| **9** | `LBA 640` - `703` | `0x0005_0000` | 32 KB | **User Application 9** | Launched via key `9` |
| - | `LBA 704` - `2047`| `0x0005_8000` | 672 KB | Unallocated MBR Gap | Free / Reserved |
| - | `LBA 2048`+ | `0x0010_0000`+ | - | **FAT32 / exFAT Partition 1** | **Fully Protected & Coexistent** |

#### VUX9 Boot Header (64 Bytes, v3)
Each slot begins at byte 0 of its starting sector with a 64-byte VUX9 v3 header (`struct.pack("<IHHIIIIII32s", ...)`; `header_version` must equal `3` or the slot is rejected), followed immediately by executable binary code:

| Offset | Field | Type | Value / Meaning |
|:---|:---|:---|:---|
| `0x00` - `0x03` | Magic Number | `u32` (LE) | `0x56555839` (`"VUX9"` ASCII) |
| `0x04` - `0x05` | Header Version | `u16` (LE) | Must be `3`; any other value is rejected by both the Resident Loader and Boot Manager |
| `0x06` - `0x07` | Slot Flags | `u16` (LE) | Bit 0 = valid (`0x1`), Bit 2 = system slot (`0x4`) |
| `0x08` - `0x0B` | ISA Mode | `u32` (LE) | `0` = Nand2Tetris Hack 16-bit, `1` = RISC-V RV32I |
| `0x0C` - `0x0F` | Binary Byte Length | `u32` (LE) | Exact size of executable binary in bytes (max 14336 B) |
| `0x10` - `0x13` | Load Address | `u32` (LE) | I-RAM destination for the payload (currently always `0x0000_0000` in tooling/tests) |
| `0x14` - `0x17` | Entry Point | `u32` (LE) | Reserved for a future non-zero resume address; parsed but not yet consumed — the CPU always resumes at `RESET_VECTOR` via soft-reset |
| `0x18` - `0x1B` | Payload CRC32 | `u32` (LE) | IEEE 802.3 CRC32 of payload; computed and verified on load/flash |
| `0x1C` - `0x1F` | Image Version | `u32` (LE) | Application/image version number, used for Boot Manager self-update comparisons |
| `0x20` - `0x3F` | Program Name | `32 bytes` | Null-terminated ASCII/UTF-8 application name |
| `0x40` - `0x1FF` | Payload (Start Sector) | `bytes` | First chunk of binary machine code (up to 448 bytes) |

Subsequent 512-byte sectors continue the payload until the full binary length is loaded into I-RAM at `0x0000_0000`.

---

## Host Tooling & Boot Manager CLI

### 1. Host Utility: `vux_tool.py`

[`tools/vux_tool.py`](tools/vux_tool.py) provides host-side management over UART (supports `--port` e.g. `/dev/ttyUSB3` or `auto`):

```bash
# 1. Hardware Self-Diagnostics (Tests onboard LEDs, User Button S2, 64-bit CLINT timer, MicroSD SPI init)
python3 tools/vux_tool.py diag

# 2. Dump Sector 0 (MBR) in Hex/ASCII & check 0x55AA signature
python3 tools/vux_tool.py dump-mbr

# 3. List All Program Slots Catalog (Inspects Slots 0-9 headers)
python3 tools/vux_tool.py list-slots

# 4. Flash Dual-ISA binary to MicroSD Card Slots (MBR Gap)
#    Each write is verified by reading the block back on-device; the host tool raises
#    a RuntimeError if the Boot Manager reports an [SD-ERR] during the flash.
#    - Flashes Hack 16-bit binary to Slot 2 with program name:
python3 tools/vux_tool.py flash-sd build/hack/firmware.bin --mode hack --slot 2 --name "HackDemo"
#    - Flashes RISC-V 32-bit binary to Slot 1 (Default S2 launch slot):
python3 tools/vux_tool.py flash-sd build/firmware/firmware.bin --mode riscv --slot 1 --name "RiscvDemo"
#    - Flashes Custom Boot Manager to Slot 0 (SD Override):
python3 tools/vux_tool.py flash-sd build/firmware/firmware.bin --mode riscv --slot 0 --name "BootManager"

# 5. Inspect specific Slot Header & verify Magic/Version/Mode/Size/Name
python3 tools/vux_tool.py inspect-sd --slot 1

# 6. Trigger MicroSD image loading & execution via Resident Loader
python3 tools/vux_tool.py boot --slot 1

# 7. Interactive Serial Monitor (115200 bps)
python3 tools/vux_tool.py monitor

# 8. Trigger a hardware FPGA reset (openFPGALoader --reset)
python3 tools/vux_tool.py reset
```

### 2. On-Chip Bare-Metal Boot Manager CLI

Connecting any terminal (115200 bps 8N1) presents the interactive `vux>` prompt:

| Command | Key | Description |
|:---|:---|:---|
| Help | `h` or `?` | Displays available command menu |
| List Catalog | `l` | Inspects Slots 0–9 headers and prints catalog (Slot, Name, ISA, Size) |
| Launch App | `1` - `9` | Launches application in Slot 1–9 via Resident Loader Mailbox |
| Write SD Slot | `w` | Receives binary stream over UART and flashes to designated slot (0–9) |
| Inspect Slot | `s`, `s0`-`s9` | Parses a slot's header (Slot 0 when no digit follows within 50 ms) |
| Dump MBR | `d` | Reads and dumps 512-byte Sector 0 formatted table with `0x55AA` signature check |
| SD Init | `i` | Forces full re-initialization of MicroSD card (`force_init`) |
| Diag | `t` | Self-diagnostics: sweeps the LEDs (check by eye), shows Button S2's state, checks 10 ms of `mtime` against `mcycle` (PASS within 1/64), and initializes the SD card |
| Knight Rider | `k` | Plays Knight Rider LED sweep animation on 6 onboard LEDs |
| Reboot | `r` | Asks the Resident Loader (`0x0000_3800`) to load Slot 0; with no valid image there it restarts the Boot Manager already in I-RAM |

> [!TIP]
> **Physical Button S2 Boot:** Pressing physical Button S2 (active-low pin 3) on the Tang Nano 9K at the Boot Manager prompt immediately launches Slot 1 without requiring UART commands!

An exception in the Boot Manager (it enables no interrupts) prints
`[TRAP] mcause=0x… mepc=0x… mtval=0x…` and stops with LEDs 1/3/5 and 0/2/4 blinking in turn;
reset the board.

### 3. Resident Loader and the mailbox

The Boot Manager asks the Resident Loader for a slot through the word at `0x2000_1FFC`
(`fw_common::mailbox`): bits 31:16 are `0xB007` to launch slot `N` (bits 7:0) or `0xA55A`
to install slot 0 as the new Boot Manager (self-update), bit 8 is set for an SDHC card.
The loader clears the word and takes any other value (0 included) as nothing to load: it
restarts the Boot Manager.

It reads the slot twice. The first read checks the header and every sector and computes
the payload's CRC32 without writing I-RAM, so on these errors the Boot Manager, still
intact in lower I-RAM, comes back to its prompt:

| Error | Meaning |
|:---|:---|
| `[RL] E1` | The slot's first sector can't be read |
| `[RL] E2` | No VUX9 v3 header (magic or header version) |
| `[RL] E3` | Payload size 0 or over 14,336 bytes |
| `[RL] E4` | A later sector of the slot can't be read |
| `[RL] E5` | The payload doesn't match the header's CRC32 |

The second read loads the payload at the header's load address. Should it fail although
the first succeeded (`[RL] E6: reload the bitstream`), part of the Boot Manager is
already overwritten: the loader stops, and only reloading the bitstream (or a power
cycle) brings the Boot Manager back.

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
# Power on like the board (firmware.hex preload) with an SD card image, UART on this terminal
python3 tools/vux_tool.py mkimg sd.img --slot 1:build/zephyr-demo/zephyr/zephyr.bin:riscv:Demo
build/emu/target/release/vux9k-emu --sd sd.img --stdio
# ... or on TCP, for vux_tool.py (--port socket://localhost:4000) or a terminal program
build/emu/target/release/vux9k-emu --sd sd.img --sd-write-back --tcp 4000
# Start an application image (a slot's payload, as flash-sd writes it) in its ISA, as the
# Resident Loader does; stop when it prints a text
build/emu/target/release/vux9k-emu --no-firmware --load build/hack/firmware.bin --mode hack --until "(100%)!"
build/emu/target/release/vux9k-emu --no-firmware --load build/zephyr-demo/zephyr/zephyr.bin --mode riscv --stdio
```

Interactive modes run at the SoC's 18 MHz, so the firmware's timeouts behave as on
hardware; `--trace FILE` logs every instruction. Profiles: `real` (default: 16 KB
I-RAM, 8 KB D-RAM, as the board) and `extended` (512 KB / 256 KB, same MMIO) —
**the extended profile is not real hardware**; it exists for programs too big for the
board, such as bc. Python tests drive the emulator through the `vux9k_emu` module
(`make emu-py`; helpers in `sim/emu/vux9k.py`).

### Zephyr boards

`zephyr_workspace/boards/vux9k/` has two targets: `vux9k`, the real board (18 MHz, the
SoC timer as `andestech,machine-timer`, tickless at 1000 ticks/s, 14 KB of I-RAM below the Resident Loader and
8 KB of D-RAM, which the link enforces), and `vux9k/vux9k/ext`, the extended profile
for the emulator only. `make build-zephyr-demo` builds the Rust demo
(`zephyr_workspace/app/`, Zephyr + a Rust staticlib) for the real board; `make
build-zephyr` builds bc_clone_rs for `ext`. `make build-zephyr-irq-echo` builds
`zephyr_workspace/irq_echo/`, an echo through the UART driver's interrupt-driven API
(RX on the machine external interrupt; see `zephyr_workspace/README.md`, "UART").

## Application Development & Releases

To write applications for the board without building the SoC, use a
[release](https://github.com/takayuki-nagata/VUX9K/releases): the bitstream, the Boot
Manager update image, the demos, `vux_tool.py`, the Zephyr board support package and the
emulator, with [`docs/APP_DEVELOPMENT.md`](docs/APP_DEVELOPMENT.md) as the guide. `make
dist` builds the same tree into `build/dist/` (`make check-dist` runs its demos on its
own emulator); CI uploads it for every push to `main`, and a release publishes one of
those after it has been tested on the board ([`docs/RELEASING.md`](docs/RELEASING.md)).

## Quickstart & Build Guide

### Prerequisites
1. **Rust Toolchain**:
   ```bash
   rustup target add riscv32i-unknown-none-elf
   cargo install cargo-llvm-cov --version 0.9.1 --locked   # make coverage-rust
   ```
2. **OSS CAD Suite** (Yosys, nextpnr, Icarus Verilog, openFPGALoader):
   [YosysHQ/oss-cad-suite-build](https://github.com/YosysHQ/oss-cad-suite-build)
3. **Veryl Compiler**:
   ```bash
   cargo install veryl --version 0.21.0
   ```
4. **Python Environment**:
   ```bash
   uv venv --python 3.13 .venv
   uv pip install cocotb pytest pyftdi pyserial
   ```

### Building & Flashing

```bash
# 1. Build Rust Boot Manager firmware
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
```

### Test Suite Architecture

Short tests run on Icarus, long SoC/GLS runs on Verilator (`SIM_UNIT` / `SIM_SOC` in the `Makefile`; Verilator needs `perl`). `make test-sim` (CI on every push/PR) runs everything below except the rows marked *slow*, which `make test-slow` (CI nightly / manual) adds.

| Level | Command | Typical Time | Verification Scope |
|:---|:---|:---|:---|
| **RTL Unit Tests** | `make sim-unit` | ~1 min | 26 cocotb modules: CPU (ALU, decoder, regfile, CSRs, LSU, next-PC, trap unit, mode detector, Hack translator and ops, RV32I smoke, branches, trap path), UART, timer, GPIO, SD SPI master, address decoder, RAM, and the SP/SDPB block-RAM cell models. Each has boundary-value and random tests against a Python model (fixed seed; *slow*: `sim-unit-random`, a new seed each day) |
| **ISA Tests** | `make test-isa` | ~20 sec | riscv-tests `rv32ui`/`rv32mi` on `tb_hex_runner`, whose RAM has the SoC's synchronous timing (56 pass; 2 known gaps, hardware misaligned access and PMP, tracked as expected failures in `scripts/run_riscv_tests.py`) |
| **GLS ISA Tests** | `make test-isa-gls` | ~1.5 min | The same riscv-tests on the gate-level `unified_cpu` netlist |
| **ACT4 Tests** | `make test-act4` (`-gls`, `-emu`) | ~2 min each | riscv-arch-test 4.1.0: self-checking tests whose expected values the Sail reference model computed for this CPU's configuration (`scripts/act4/`), on RTL, netlist and emulator (73 pass; 4 known differences from the Sail configuration, tracked in `scripts/run_riscv_tests.py`). `make act4-elfs` generates them with the upstream image (docker or podman) |
| **GLS Unit Tests** | `make sim-gls-unit` | ~20 sec | Gowin primitive netlists (`sim/gowin_cells_sim.veryl`) of the CPU, UART controller and mode detector |
| **SoC Boot** | `make sim-soc-fast` / `sim-soc-fast-icarus` | ~10 sec / ~3 min | Power-on reset, Boot Manager prompt, S2-button launch of an SD slot via the Resident Loader (Verilator / 4-state Icarus) |
| **SoC MMIO** | `make sim-soc-mmio` | ~5 sec | RV32I program from I-RAM checking the memory map, D-RAM lanes, timer, GPIO, UART RX and status flags, soft reset; timer and UART RX interrupts |
| **Boot Manager CLI** | `make sim-boot` | ~40 sec | Every CLI command's exact output, incl. reboot via the Resident Loader |
| **Hack Mode** | `make sim-hack-rtl` | ~2 sec | Hack C firmware's self-test report over the UART |
| **SD Edge Cases** | `make sim-sd-quirks` | ~20 sec | Strict SD model: power-up preamble, SDSC byte addressing across a reboot |
| **End-to-End Flow** | `make sim-hw-flow` | ~20 sec | UART `w` flashing of Hack + RISC-V images, byte-exact SD check, header verify, boot of the flashed slot |
| **GLS SoC Boot** | `make sim-soc-gls-fast` | ~1.5 min | Full Gowin netlist boots to the Boot Manager prompt (Safe Mode) |
| *slow* **GLS End-to-End** | `make sim-gls-hw-flow` | ~3-6 min | The end-to-end flow on the full Gowin netlist |
| *slow* **Icarus End-to-End** | `make sim-hw-flow-icarus` | ~15 min | The end-to-end flow on 4-state Icarus |
| **Emulator** | `make emu-test` / `test-isa-emu` | ~10 sec | Rust unit tests of the emulator; riscv-tests on it (same results as `test-isa`) |
| **Firmware on the Emulator** | `make test-emu` | ~30 sec | Boot Manager CLI and error paths, flashing through `vux_tool.py`, Resident Loader E1-E6, self-update, Hack demo, Zephyr demo and interrupt-driven UART echo from SD, bc (extended profile), SD transcripts |
| **Firmware Host Tests** | `make test-fw-host` / `firmware-size` | ~5 sec | `firmware/fw_common` on the host; Boot Manager ≤ 14 KB, Resident Loader ≤ 2 KB |
| **Firmware Coverage** | `make coverage-fw` | ~40 sec | Source-line coverage of the firmware and demos from the emulator tests, against `coverage/thresholds.toml` (report in `build/coverage/fw/`) |
| **Rust Coverage** | `make coverage-rust` | a few min | Line coverage of the emulator (its cargo tests and the Python-driven emulator runs) and `fw_common` (host tests) with cargo-llvm-cov, against `coverage/thresholds.toml` (report in `build/coverage/rust/`) |
| **RTL ↔ Emulator Lockstep** | `make sim-lockstep` | ~40 sec | Random RV32I, traps/interrupts, Hack demo and the firmware on RTL and emulator, compared cycle by cycle (*slow*: `sim-lockstep-slow`, more programs) |
| **Zephyr Demo** | `make sim-zephyr-demo-rtl` | ~45 sec | The Zephyr Rust demo booted from SD on the RTL (*slow*: `sim-zephyr-demo-gls` on the netlist) |
| **Zephyr UART Interrupts** | `make sim-zephyr-irq-echo-rtl` | ~1 min | The interrupt-driven UART echo booted from SD on the RTL: a 64-byte line, longer than both FIFOs, comes back with no overrun |
| **Static Timing (STA)**| `make sta` | ~15 sec | Exhaustive post-PnR timing analysis, Fmax verification, and critical path breakdown (`build/synth/soc_sta.json`) |
| **Real Hardware** | `make test-hw` | ~60 sec | Automated physical hardware execution on Tang Nano 9K via `scripts/test_hardware.py` (15 tests) |
| **Board UART Interrupts** | `make test-hw-irq-echo` | ~20 sec | The interrupt-driven UART echo on the board (`scripts/hw_irq_echo.py`): flashed to slot 3 through the running Boot Manager, five 256-byte lines, then the FPGA is reconfigured from `build/synth/pack.fs` |

### Real Hardware Test Suite (15 Automated Checks)
`scripts/test_hardware.py` automatically executes an exhaustive 15-step hardware validation workflow:
1. **UART Connection & Prompt Synchronization** (`vux> ` prompt sync)
2. **Hardware Self-Diagnostics** (`t` / `vux_tool.run_diag`: LED animation, user button S2, MicroSD SPI init, 64-bit CLINT timer)
3. **MicroSD Sector 0 (MBR) Dump** (`d` / `vux_tool.dump_mbr`: 512-byte read & `0x55AA` signature validation)
4. **Flash Slot 0: Boot Manager** (`w` / `vux_tool.flash_slot`: 23 sectors to LBA 64)
5. **Header Verification: Slot 0** (`s0` / `vux_tool.inspect_slot`: Magic `VUX9`, Mode 1/RISC-V)
6. **Flash Slot 1: Default RISC-V App** (`w` / `vux_tool.flash_slot`: 4 sectors to LBA 128)
7. **Header Verification: Slot 1** (`s1` / `vux_tool.inspect_slot`: Magic `VUX9`, Mode 1/RISC-V)
8. **Flash Slot 2: Hack 16-bit Firmware** (`w` / `vux_tool.flash_slot`: 6 sectors to LBA 192)
9. **Header Verification: Slot 2** (`s2` / `vux_tool.inspect_slot`: Magic `VUX9`, Mode 0/Hack)
10. **Program Slots Catalog Listing** (`l` / `vux_tool.list_slots`: Multi-slot catalog listing)
11. **Negative Test: CRC32 Corrupted Payload Rejection** (Slot 0, `crc_override`: rejects a corrupted image before it's accepted)
12. **Negative Test: Invalid Magic Header Rejection** (Slot 3, `magic_override`: rejects a header without `VUX9` magic)
13. **Boot Manager Self-Update & Version Rollback** (Flashes Slot 0 with a newer `version`, verifies in-place I-RAM update, then rolls back to the original version)
14. **Boot Slot 1: Zephyr Rust App Execution** (`1` / `vux_tool.boot_slot`: the Zephyr Rust demo, `make build-zephyr-demo`)
15. **Boot Slot 2: Hack 16-bit Firmware Execution** (`2` / `vux_tool.boot_slot`: Hack C firmware execution and test pass)

> [!NOTE]
> After steps 14 and 15, `test_hardware.py` reloads the SRAM bitstream via `openFPGALoader`: a launched application never returns to the Boot Manager, and reconfiguring the FPGA is the way back. Before the tests, it warns when other full/low-speed USB devices share the board's USB hub, which makes the board's USB-UART bridge drop output (see `docs/APP_DEVELOPMENT.md`).

### Board Smoke Test of Routed Seeds (`make hw-smoke`)

A passing STA does not prove a bitstream works (see "Clock"), so check the placement
itself on the board before adopting a seed. `make timing` routes every seed into
`build/synth/pnr/seed_<N>/`; `make hw-smoke` (`scripts/hw_smoke.py`) then, for each
seed, replaces only the I-RAM block RAMs' initial contents with `firmware/hw_test`,
packs, loads the result into SRAM and reads the verdict. The placement and routing
stay exactly those of the seed; the SD card and the boot chain are not involved.

`hw_test` runs eleven instruction-pattern checksums (add, sub, logic, slt, shifts,
branches, dependent sub/branch loops, back-to-back dependencies, loads/stores of every
width, all registers) against the values the emulator computes, one line each, then
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
│   └── soc_top.veryl               # Tang Nano 9K SoC top-level wrapper
├── firmware/                       # Cargo workspace: bare-metal Rust boot firmware
│   ├── Cargo.toml                  # Workspace manifest (members: boot_manager, resident_loader)
│   ├── boot_manager/                   # Boot Manager & drivers (14KB at 0x0000_0000)
│   │   ├── Cargo.toml
│   │   ├── bootstrap/                  # Assembly start.s & linker script link.x
│   │   └── src/                        # MicroSD SPI driver, UART CLI, catalog manager, flasher
│   ├── resident_loader/                # Resident Loader (2KB at 0x0000_3800)
│   ├── fw_common/                      # Boot Manager logic without MMIO, tested on the host
│   └── hw_test/                        # Board self-test for make hw-smoke (replaces the Boot Manager in BRAM)
├── hack_demo/                      # Hack 16-bit C/Assembly demo app (toolchain self-test)
├── zephyr_workspace/               # Zephyr module: vux9k boards, SoC, UART driver, dts (Apache-2.0, own README)
│   ├── app/                        # Zephyr Rust demo for the real board (rust_demo staticlib)
│   └── irq_echo/                   # Zephyr interrupt-driven UART echo (C) for the real board
├── docs/                           # APP_DEVELOPMENT.md (application developers), RELEASING.md
├── emu/                            # Rust emulator: core, CLI (vux9k-emu), Python module (vux9k_emu)
├── sim/                            # cocotb & pytest testbenches (run via sim/runners/test_sim.py)
│   ├── emu/                        # pytest tests on the emulator (firmware, demos, lockstep compare)
│   └── sd_transcripts/             # SD card exchanges both card models must reproduce
├── coverage/                       # thresholds.toml: minimum coverage per image
├── scripts/                        # Build/CI plumbing (elf2bin.py, run_riscv_tests.py, test_hardware.py, run_pnr.py, report_sta.py, ...)
├── tools/                          # End-user CLI: vux_tool.py (UART flashing, diagnostics, monitor)
├── vendor/                         # bc_clone_rs (git submodule); riscv-tests (fetched on demand by run_riscv_tests.py, gitignored)
└── build/                          # Unified build output (gitignored) - every generated artifact lands here
    ├── veryl/                      # veryl build --out-dir output: generated .sv/.map for soc/, soc/cpu/, soc/uart/, sim/
    ├── firmware/                   # firmware.bin/.hex, firmware_d0-3.hex, resident_loader.bin
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
