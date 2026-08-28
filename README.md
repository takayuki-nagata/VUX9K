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
|  |   |  Auto-Mode Detector   | --> | 2-Cycle Multi-FSM | --> | Shared ALU  |   |  |
|  |   | (RV32I vs Hack 16-bit)|     | (FETCH -> EXEC)   |     | (32-bit ops)|   |  |
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

---

## Lineage & Original Projects

The RTL modules in this repository were originally authored in VHDL-2008 and have been fully ported to modern Veryl:

- **CPU Core (`unified_cpu`)**: Ported from [`takayuki-nagata/hack_cpu`](https://github.com/takayuki-nagata/hack_cpu)
  - Unified datapath supporting both **RISC-V RV32I** (32-bit) and **Nand2Tetris Hack** (16-bit) execution with dynamic ISA auto-detection.
- **UART Controller (`uart_controller`)**: Ported from [`takayuki-nagata/uart_controller`](https://github.com/takayuki-nagata/uart_controller)
  - Parameterized baud rate clock timer (27.0 MHz -> 115200 bps), 8N1 serial framing, and dual 16-byte synchronous TX/RX FIFOs.
- **Arbitrary-Precision Math Engine & REPL (`vendor/bc_clone_rs`)**: Submodule from [`takayuki-nagata/bc_clone_rs`](https://github.com/takayuki-nagata/bc_clone_rs)
  - Embedded `bc_core` arbitrary-precision arithmetic engine with 10/10 math test suite running atop Zephyr RTOS.
- **Hack Toolchain & C Firmware (`firmware_hack`)**: Powered by [`takayuki-nagata/hack_tools`](https://github.com/takayuki-nagata/hack_tools) (`has` assembler & `m2h` transpiler)
  - 16-bit C and Hack assembly firmware testing recursive arithmetic, Fibonacci, array manipulations, and MMIO UART output.

---

## Memory & MMIO Register Map

### System Address Space
| Address Range | Size | Component | Description |
|:---|:---|:---|:---|
| `0x0000_0000` - `0x0000_3FFF` | 16 KB | **Instruction RAM / ROM** | Preloaded with Rust Boot Manager; executable target for SD app loading |
| `0x2000_0000` - `0x2000_1FFF` | 8 KB | **Data RAM** | `.data`, `.bss`, stack, and heap region |
| `0x4000_0000` - `0x4000_000F` | 16 B | **UART Controller** | Full-duplex 115200 bps TX/RX data registers & status flags |
| `0x4000_1000` - `0x4000_101F` | 32 B | **System Timer (CLINT)** | 64-bit `mtime` and `mtimecmp` registers |
| `0x4000_2000` - `0x4000_200F` | 16 B | **MicroSD SPI Master** | SPI TX/RX data, CS assertion, busy status, clock divider |
| `0x4000_3000` - `0x4000_300F` | 16 B | **GPIO Controller** | 6 onboard active-low LEDs (`0x00`=ON, `0x3F`=OFF) & user buttons |

### MicroSD Card Sector Map (Filesystem-Safe MBR Gap Boot)
| LBA Sector | Byte Offset | Allocation / Purpose | Filesystem Safety |
|:---|:---|:---|:---|
| `LBA 0` | `0x0000_0000` (512 B) | **MBR (Master Boot Record)** | Protected (Untouched by tool) |
| `LBA 1` - `LBA 63` | `0x0000_0200` - `0x0000_7FFF` | Reserved Partition Header Area | Protected |
| **`LBA 64` - `LBA 2047`** | **`0x0000_8000` - `0x000F_FFFF`** | **VUX9 Boot Image (MBR Gap ~1 MB)** | **Dedicated VUX9 Boot Location** |
| `LBA 2048`+ | `0x0010_0000`+ | **FAT32 / exFAT Partition 1** | **Fully Protected & Coexistent** |

#### VUX9 Boot Header (Sector 64)
| Offset | Field | Value / Meaning |
|:---|:---|:---|
| `0x00` - `0x03` | Magic Number | `0x56555839` (`"VUX9"` ASCII) |
| `0x04` | ISA Mode | `0` = Hack 16-bit, `1` = RISC-V RV32I |
| `0x05` | Entry Point Offset | Word offset in I-RAM (typically `0x00`) |
| `0x06` - `0x07` | Sector Count | Number of 512-byte payload sectors (`n`) |
| `0x08` - `0x0B` | Binary Byte Length | Exact size of executable binary in bytes |
| `0x0C` - `0x0F` | CRC32 Checksum | Integrity check for payload |
| `0x10` - `0x1FF` | Payload (Start) | First chunk of binary machine code |

---

## Host Tooling: `vux_tool.py`

[`scripts/vux_tool.py`](scripts/vux_tool.py) is a comprehensive CLI management utility for Tang Nano 9K:

```bash
# 1. Hardware Self-Diagnostics (Tests onboard LEDs, UART loopback, MicroSD SPI init)
python3 scripts/vux_tool.py diag

# 2. Dump Sector 0 (MBR) in Hex/ASCII
python3 scripts/vux_tool.py dump-mbr

# 3. Flash Dual-ISA binary to MicroSD Card Sector 64 (MBR Gap)
#    - Flashes Hack 16-bit binary:
python3 scripts/vux_tool.py flash-sd build_hack/firmware.bin --mode hack
#    - Flashes RISC-V 32-bit binary:
python3 scripts/vux_tool.py flash-sd firmware/firmware.bin --mode riscv

# 4. Inspect Sector 64 Header & verify Magic/CRC32
python3 scripts/vux_tool.py inspect-sd

# 5. Trigger MicroSD image loading & execution
python3 scripts/vux_tool.py boot

# 6. Interactive Serial Monitor (115200 bps)
python3 scripts/vux_tool.py monitor
```

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
   cargo install veryl --version 0.20.3
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
# 1. Full Verification Suite (All Simulation Suites + Static Timing Analysis STA)
make test

# 2. Simulation-Only Verification Suite (RTL, Arch Compliance & GLS Netlists)
make test-sim

# 3. Static Timing Analysis (STA) & Physical Timing Closure (27.0 MHz)
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
| **RTL Unit Tests** | `make sim-unit` | ~25 sec | 14 testbenches verifying CPU, ALU, Decoder, Register File, CSRs, UART, FIFO, and Auto-Mode detector |
| **Arch Compliance** | `make test-arch-compliance` | ~15 sec | Official RISC-V architectural compliance suite (45/45 tests passed 100%) |
| **GLS Unit Tests** | `make sim-gls-unit` | ~40 sec | Gowin primitive netlists (`gowin_cells_sim.v`) with LUTRAMs (`RAM16SDP4`), ALUs, and DFFs |
| **Fast SoC Boot** | `make sim-soc-fast` / `make sim-soc-gls-fast` | < 1 sec | Fast power-on reset, CPU boot, and instruction execution verification on RTL & full Gowin netlist |
| **End-to-End Flow** | `make sim-hw-flow` / `make sim-gls-hw-flow` | ~3-4 min | Full 8-step hardware test suite in simulation using `VirtualSerialBridge` (UART) and `SpiSdCardModel` (SD SPI) |
| **Static Timing (STA)**| `make sta` | ~15 sec | Exhaustive post-PnR timing analysis, Fmax verification, and critical path breakdown (`soc_sta.json`) |
| **Real Hardware** | `make test-hw` | ~25 sec | Automated physical hardware execution on Tang Nano 9K via `scripts/test_hardware.py` |

### Real Hardware & Full Flow Test Cases (8 Items)
Both `scripts/test_hardware.py` (real board) and `sim/test_soc_hardware_flow.py` (simulation) execute the complete 8-step verification flow:
1. **UART Connection & Prompt Synchronization** (`vux> `)
2. **Hardware Self-Diagnostics** (`diag` / `t`: LED animation, user button, MicroSD SPI init, 64-bit CLINT timer)
3. **MicroSD Sector 0 (MBR) Dump** (`dump-mbr` / `d`: 512-byte read & `0x55AA` signature validation)
4. **Multi-Sector Flash: Hack 16-bit Firmware** (`flash-sd` / `w` to Sector 64 MBR gap)
5. **Header Verification: Hack 16-bit** (`inspect-sd` / `s`: Magic `VUX9`, Mode `0`)
6. **Multi-Sector Flash: RISC-V 32-bit Firmware** (`flash-sd` / `w` to Sector 64 MBR gap)
7. **Header Verification: RISC-V 32-bit** (`inspect-sd` / `s`: Magic `VUX9`, Mode `1`)
8. **MicroSD Program Load & Execution Trigger** (`boot` / `l`: Zero-overhead payload transfer to I-RAM and jump)

---

## Directory Structure

```
VUX9K/
├── Makefile                        # Unified build, test, synthesis, and flash automation
├── Veryl.toml                      # Veryl project configuration & dependencies
├── cpu/                            # Veryl Unified CPU & Dual-ISA modules
│   ├── auto_mode_detector.veryl    # First-instruction ISA auto-detector
│   ├── hack_translator.veryl       # Hack 16-bit instruction to uOp translator
│   ├── rv32i_alu.veryl             # Shared 32-bit ALU
│   ├── rv32i_csrs.veryl            # Machine-Mode CSRs (mstatus, mie, mtvec, mepc, mcause)
│   ├── rv32i_decode.veryl          # RV32I instruction decoder & immediate generator
│   ├── rv32i_pkg.veryl             # Common type definitions and opcodes
│   ├── rv32i_regfile.veryl         # Dual-port 32 x 32-bit register file (Distributed RAM)
│   └── unified_cpu.veryl           # 2-cycle multi-cycle Dual-ISA CPU Top Module
├── uart/                           # Veryl UART Controller & FIFOs
│   ├── clk_timer.veryl             # Parameterized baud rate clock timer
│   ├── fifo_sync.veryl             # 16-entry synchronous FIFO buffer
│   ├── shift_registers.veryl       # Parallel load shift register
│   ├── uart_tx.veryl               # 8N1 serial transmitter
│   ├── uart_rx.veryl               # 8N1 serial receiver
│   └── uart_controller.veryl       # Integrated UART subsystem
├── soc/                            # Veryl SoC Top Level & Interconnect
│   ├── gpio_controller.veryl       # LED & button GPIO controller
│   ├── sdcard_spi.veryl            # MicroSD SPI Master controller
│   ├── timer_core.veryl            # 64-bit mtime/mtimecmp timer core
│   ├── soc_ram.veryl               # Harvard 16KB I-RAM + 8KB D-RAM memory
│   └── soc_top.veryl               # Tang Nano 9K SoC top-level wrapper
├── firmware/                       # Bare-metal Rust Boot Manager & drivers
│   ├── Cargo.toml
│   ├── bootstrap/                  # Assembly start.s & linker script link.x
│   └── src/                        # MicroSD SPI driver, UART CLI, and flasher
├── firmware_hack/                  # Hack 16-bit C and Assembly test suite
├── sim/                            # Cocotb & Pytest RTL simulation testbenches
├── scripts/                        # Host tooling & flasher (vux_tool.py, run_arch_test.py)
└── vendor/                         # Submodules (bc_clone_rs, riscv-arch-test)
```

---

## License

This project is licensed under the **[MIT License](LICENSE)** (see [`LICENSES/MIT.txt`](LICENSES/MIT.txt)).
Zephyr RTOS Out-of-Tree components are licensed under the **[Apache License 2.0](LICENSES/Apache-2.0.txt)**.
All files include SPDX headers conforming to the [REUSE](https://reuse.software/) specification.
