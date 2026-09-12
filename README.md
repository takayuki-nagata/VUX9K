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
| | 20KB  |         |  8KB    |         | UART    |        | Timer   |       | SD | |
| | I-RAM |         |  D-RAM  |         | 115200  |        | 64-bit  |       | SPI| |
| +-------+         +---------+         +---------+        +---------+       +----+ |
+----+-------------------+-------------------+------------------+-----------------+-+
     |                   |                   |                  |                 |
 [Reset/Boot]        [Stack/Data]        [USB-UART]         [mtime/cmp]      [MicroSD]
```

---

## Lineage & Original Projects

The RTL modules in this repository were originally authored in VHDL-2008 and have been fully ported to modern Veryl:

- **CPU Core (`unified_cpu`)**: Ported from [`takayuki-nagata/hack_cpu`](https://github.com/takayuki-nagata/hack_cpu)
  - Unified datapath supporting both **RISC-V RV32I** (32-bit) and **Nand2Tetris Hack** (16-bit) execution with dynamic ISA auto-detection.
- **UART Controller (`uart_controller`)**: Ported from [`takayuki-nagata/uart_controller`](https://github.com/takayuki-nagata/uart_controller)
  - Parameterized baud rate clock timer (27.0 MHz -> 115200 bps), 8N1 serial framing, and dual 32-entry synchronous TX/RX FIFOs.
- **Arbitrary-Precision Math Engine & REPL (`vendor/bc_clone_rs`)**: Submodule from [`takayuki-nagata/bc_clone_rs`](https://github.com/takayuki-nagata/bc_clone_rs)
  - Embedded `bc_core` arbitrary-precision arithmetic engine with 10/10 math test suite running atop Zephyr RTOS.
- **Hack Toolchain & C Firmware (`firmware_hack`)**: Powered by [`takayuki-nagata/hack_tools`](https://github.com/takayuki-nagata/hack_tools) (`has` assembler & `m2h` transpiler)
  - 16-bit C and Hack assembly firmware testing recursive arithmetic, Fibonacci, array manipulations, and MMIO UART output.

---

## Memory & MMIO Register Map

### System Address Space
| Address Range | Size | Component | Description |
|:---|:---|:---|:---|
| `0x0000_0000` - `0x0000_47FF` | 18 KB | **Instruction RAM (Lower)** | Preloaded with Rust Boot Manager (factory fallback); target region for SD slot application execution |
| `0x0000_4800` - `0x0000_4FFF` | 2 KB | **Resident Loader (Upper I-RAM)** | Immutable resident bootloader at `RESET_VECTOR = 0x0000_4800`; checks Mailbox and loads SD slots |
| `0x2000_0000` - `0x2000_1FFF` | 8 KB | **Data RAM** | `.data`, `.bss`, stack, and heap. Mailbox register located at `0x2000_1FFC` |
| `0x4000_0000` - `0x4000_000F` | 16 B | **UART Controller** | Full-duplex 115200 bps TX/RX data registers & status flags |
| `0x4000_1000` - `0x4000_101F` | 32 B | **System Timer (CLINT)** | 64-bit `mtime` and `mtimecmp` registers |
| `0x4000_2000` - `0x4000_200F` | 16 B | **MicroSD SPI Master** | SPI TX/RX data, CS assertion, busy status, clock divider |
| `0x4000_3000` - `0x4000_300F` | 16 B | **GPIO Controller** | 6 onboard active-low LEDs (`0x00`=ON, `0x3F`=OFF) & user buttons (Button S2 on pin 3) |

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

#### VUX9 Boot Header (64 Bytes)
Each slot begins at byte 0 of its starting sector with a 64-byte VUX9 v2 header (`struct.pack("<IIII32s16s", ...)`), followed immediately by executable binary code:

| Offset | Field | Type | Value / Meaning |
|:---|:---|:---|:---|
| `0x00` - `0x03` | Magic Number | `u32` (LE) | `0x56555839` (`"VUX9"` ASCII) |
| `0x04` - `0x07` | Header Version | `u32` (LE) | `1` (legacy 16-byte) or `2` (extended 64-byte) |
| `0x08` - `0x0B` | Binary Byte Length | `u32` (LE) | Exact size of executable binary in bytes (max 20480 B) |
| `0x0C` - `0x0F` | ISA Mode | `u32` (LE) | `0` = Nand2Tetris Hack 16-bit, `1` = RISC-V RV32I |
| `0x10` - `0x2F` | Program Name | `32 bytes` | Null-terminated ASCII/UTF-8 application name |
| `0x30` - `0x3F` | Reserved / Flags | `16 bytes` | Reserved (`0x0000_0000...`) |
| `0x40` - `0x1FF` | Payload (Start Sector) | `bytes` | First chunk of binary machine code (up to 448 bytes) |

Subsequent 512-byte sectors continue the payload until the full binary length is loaded into I-RAM at `0x0000_0000`.

---

## Host Tooling & Boot Manager CLI

### 1. Host Utility: `vux_tool.py`

[`scripts/vux_tool.py`](scripts/vux_tool.py) provides host-side management over UART (supports `--port` e.g. `/dev/ttyUSB3` or `auto`):

```bash
# 1. Hardware Self-Diagnostics (Tests onboard LEDs, User Button S2, 64-bit CLINT timer, MicroSD SPI init)
python3 scripts/vux_tool.py diag

# 2. Dump Sector 0 (MBR) in Hex/ASCII & check 0x55AA signature
python3 scripts/vux_tool.py dump-mbr

# 3. List All Program Slots Catalog (Inspects Slots 0-9 headers)
python3 scripts/vux_tool.py list-slots

# 4. Flash Dual-ISA binary to MicroSD Card Slots (MBR Gap)
#    - Flashes Hack 16-bit binary to Slot 2 with program name:
python3 scripts/vux_tool.py flash-sd build_hack/firmware.bin --mode hack --slot 2 --name "HackDemo"
#    - Flashes RISC-V 32-bit binary to Slot 1 (Default S2 launch slot):
python3 scripts/vux_tool.py flash-sd firmware/firmware.bin --mode riscv --slot 1 --name "RiscvDemo"
#    - Flashes Custom Boot Manager to Slot 0 (SD Override):
python3 scripts/vux_tool.py flash-sd firmware/firmware.bin --mode riscv --slot 0 --name "BootManager"

# 5. Inspect specific Slot Header & verify Magic/Version/Mode/Size/Name
python3 scripts/vux_tool.py inspect-sd --slot 1

# 6. Trigger MicroSD image loading & execution via Resident Loader
python3 scripts/vux_tool.py boot --slot 1

# 7. Interactive Serial Monitor (115200 bps)
python3 scripts/vux_tool.py monitor
```

### 2. On-Chip Bare-Metal Boot Manager CLI

Connecting any terminal (115200 bps 8N1) presents the interactive `vux>` prompt:

| Command | Key | Description |
|:---|:---|:---|
| Help | `h` or `?` | Displays available command menu |
| List Catalog | `l` | Inspects Slots 0–9 headers and prints catalog (Slot, Name, ISA, Size) |
| Launch App | `1` - `9` | Launches application in Slot 1–9 via Resident Loader Mailbox |
| Write SD Slot | `w` | Receives binary stream over UART and flashes to designated slot (0–9) |
| Inspect Slot | `s` | Parses Slot 0 Boot Manager Header |
| Dump MBR | `d` | Reads and dumps 512-byte Sector 0 formatted table with `0x55AA` signature check |
| SD Init | `i` | Forces full re-initialization of MicroSD card (`force_init`) |
| Diag | `t` | Runs self-diagnostics (LED pattern, Button S2 state, CLINT 10ms timing, SD SPI) |
| Knight Rider | `k` | Plays Knight Rider LED sweep animation on 6 onboard LEDs |
| Reboot | `r` | Reboots the SoC back to Resident Loader (`0x0000_4800`) |

> [!TIP]
> **Physical Button S2 Boot:** Pressing physical Button S2 (active-low pin 3) on the Tang Nano 9K at the Boot Manager prompt immediately launches Slot 1 without requiring UART commands!

---

## Quickstart & Build Guide

### Prerequisites
1. **Rust Toolchain**:
   ```bash
   rustup target add riscv32i-unknown-none-elf
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

# 2. Synthesize Veryl SoC RTL -> GW1NR-9 Bitstream (pack.fs)
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

# 3. Static Timing Analysis (STA) & Physical Timing Closure (27.0 MHz, strict check)
make sta

# 4. End-to-End Virtual Hardware Simulation Flow (RTL & GLS on-demand)
make sim-hw-flow
make sim-gls-hw-flow

# 5. Hardware Target: Synthesize, Flash SRAM & Run Automated Real-Board Test Suite on Tang Nano 9K
make test-hw
```

### Test Suite Architecture

| Level | Command | Typical Time | Verification Scope |
|:---|:---|:---|:---|
| **RTL Unit Tests** | `make sim-unit` | ~25 sec | 15 testbenches verifying CPU, ALU, Decoder, Register File, CSRs, UART, FIFO, and Auto-Mode detector |
| **Arch Compliance** | `make test-arch-compliance` | ~15 sec | Official RISC-V architectural compliance suite (45/45 tests passed 100%) |
| **GLS Unit Tests** | `make sim-gls-unit` | ~40 sec | Gowin primitive netlists (`gowin_cells_sim.v`) with LUTRAMs (`RAM16SDP4`), ALUs, and DFFs |
| **Fast SoC Boot** | `make sim-soc-fast` / `make sim-soc-gls-fast` | < 1 sec | Fast power-on reset, CPU boot, and instruction execution verification on RTL & full Gowin netlist |
| **End-to-End Flow** | `make sim-hw-flow` / `make sim-gls-hw-flow` | ~3-4 min | Full 8-step hardware test suite in simulation using `VirtualSerialBridge` (UART) and `SpiSdCardModel` (SD SPI) |
| **Static Timing (STA)**| `make sta` | ~15 sec | Exhaustive post-PnR timing analysis, Fmax verification, and critical path breakdown (`soc_sta.json`) |
| **Real Hardware** | `make test-hw` | ~30 sec | Automated physical hardware execution on Tang Nano 9K via `scripts/test_hardware.py` (11/11 PASS 100%) |

### Real Hardware Test Suite (11 Automated Checks)
`scripts/test_hardware.py` automatically executes an exhaustive 11-step hardware validation workflow:
1. **UART Connection & Prompt Synchronization** (`vux> `)
2. **Hardware Self-Diagnostics** (`diag` / `t`: LED animation, user button S2, MicroSD SPI init, 64-bit CLINT timer)
3. **MicroSD Sector 0 (MBR) Dump** (`dump-mbr` / `d`: 512-byte read & `0x55AA` signature validation)
4. **Multi-Sector Flash: Slot 2 (Hack 16-bit)** (`flash-sd --slot 2 --name "HackDemo"`)
5. **Multi-Sector Flash: Slot 1 (RISC-V 32-bit)** (`flash-sd --slot 1 --name "RiscvDemo"`)
6. **Multi-Sector Flash: Slot 0 (Boot Manager SD Override)** (`flash-sd --slot 0 --name "BootMgrSD"`)
7. **Catalog Listing Verification** (`list-slots` / `l`: Validate catalog output for Slots 0, 1, 2)
8. **Slot Header Inspections** (`inspect-sd --slot 0/1/2`: Verify Magic `VUX9`, ISA modes, byte lengths, program names)
9. **Boot & Execution Verification: Slot 1** (`boot --slot 1`: Resident loader mailbox boot & UART banner check)
10. **Boot & Execution Verification: Slot 2** (`boot --slot 2`: Resident loader mailbox boot & UART banner check)
11. **Return to Boot Manager Prompt & Slot 0 Cleanup** (Restore pristine state)

---

## Directory Structure

```
VUX9K/
├── Makefile                        # Unified build, test, synthesis, and flash automation
├── Veryl.toml                      # Veryl project configuration & dependencies
├── tangnano9k.cst                  # Physical pin constraints for Tang Nano 9K (GW1NR-9)
├── cpu/                            # Veryl Unified CPU & Dual-ISA modules
│   ├── auto_mode_detector.veryl    # First-instruction ISA auto-detector
│   ├── hack_translator.veryl       # Hack 16-bit instruction to uOp translator
│   ├── rv32i_alu.veryl             # Shared 32-bit ALU
│   ├── rv32i_csrs.veryl            # Machine-Mode CSRs (mstatus, mie, mtvec, mepc, mcause)
│   ├── rv32i_decode.veryl          # RV32I instruction decoder & immediate generator
│   ├── rv32i_pkg.veryl             # Common type definitions and opcodes
│   ├── rv32i_regfile.veryl         # Dual-port 32 x 32-bit register file (Distributed RAM)
│   └── unified_cpu.veryl           # Multi-cycle Dual-ISA CPU Top Module (FETCH, EXECUTE, MEM_WAIT)
├── uart/                           # Veryl UART Controller & FIFOs
│   ├── clk_timer.veryl             # Parameterized baud rate clock timer
│   ├── fifo_sync.veryl             # 32-entry synchronous FIFO buffer
│   ├── shift_registers.veryl       # Parallel load shift register
│   ├── uart_tx.veryl               # 8N1 serial transmitter
│   ├── uart_rx.veryl               # 8N1 serial receiver
│   └── uart_controller.veryl       # Integrated UART subsystem
├── soc/                            # Veryl SoC Top Level & Interconnect
│   ├── gpio_controller.veryl       # LED & button GPIO controller
│   ├── sdcard_spi.veryl            # MicroSD SPI Master controller
│   ├── timer_core.veryl            # 64-bit mtime/mtimecmp timer core
│   ├── soc_ram.veryl               # Harvard 20KB I-RAM + 8KB D-RAM memory
│   └── soc_top.veryl               # Tang Nano 9K SoC top-level wrapper
├── resident_loader/                # Bare-metal Rust Resident Loader (2KB at 0x0000_4800)
├── firmware/                       # Bare-metal Rust Boot Manager & drivers (18KB at 0x0000_0000)
│   ├── Cargo.toml
│   ├── bootstrap/                  # Assembly start.s & linker script link.x
│   └── src/                        # MicroSD SPI driver, UART CLI, catalog manager, flasher
├── firmware_hack/                  # Hack 16-bit C and Assembly test suite
├── zephyr_workspace/               # Zephyr RTOS out-of-tree application & bc_clone_rs integration
├── sim/                            # Cocotb & Pytest RTL simulation testbenches
├── scripts/                        # Host tooling & flasher (vux_tool.py, run_arch_test.py, test_hardware.py)
└── vendor/                         # Submodules (bc_clone_rs, riscv-arch-test)
```

---

## License

This project is licensed under the **[MIT License](LICENSE)** (see [`LICENSES/MIT.txt`](LICENSES/MIT.txt)).
Zephyr RTOS Out-of-Tree components are licensed under the **[Apache License 2.0](LICENSES/Apache-2.0.txt)**.
All files include SPDX headers conforming to the [REUSE](https://reuse.software/) specification.
