# VUX9K

**VUX9K** (Veryl Unified eXecution on Tang Nano 9K) is a **Dual-ISA (RISC-V RV32I & Nand2Tetris Hack 16-bit) System-on-Chip (SoC)** written 100% in **[Veryl](https://github.com/veryl-lang/veryl)** targeting the **Sipeed Tang Nano 9K** FPGA board. 

The SoC is designed with future **Zephyr RTOS** and **Rust application execution** in mind, featuring a Harvard Architecture with SD-Card-backed instruction loading, MMIO UART, System Timer (`mtime`/`mtimecmp`), dual-mode CPU instruction execution, and complete unit / compliance / SoC integration test suites.

---

## Lineage & Original Projects

The RTL modules in this repository were originally authored in VHDL-2008 and have been fully ported to Veryl, preserving and extending comprehensive test suites:

- **CPU Core (`unified_cpu`)**: Ported from [`takayuki-nagata/hack_cpu`](https://github.com/takayuki-nagata/hack_cpu)
  - Features dual-ISA execution with hardware auto-detection of RISC-V RV32I (32-bit) and Nand2Tetris Hack (16-bit) machine code.
- **UART Controller (`uart_controller`)**: Ported from [`takayuki-nagata/uart_controller`](https://github.com/takayuki-nagata/uart_controller)
  - Features full-duplex 8N1 serial communication, parameterized clock prescalers, and TX/RX synchronous FIFO buffers.
- **Arbitrary-Precision Math Engine & REPL (`vendor/bc_clone_rs`)**: Submodule from [`takayuki-nagata/bc_clone_rs`](https://github.com/takayuki-nagata/bc_clone_rs)
  - Provides `bc_core` arbitrary-precision arithmetic engine, self-test suite, and interactive REPL running atop Zephyr RTOS (`examples/zephyr_app`).
- **Hack Toolchain & C Firmware (`firmware_hack`)**: Powered by [`takayuki-nagata/hack_tools`](https://github.com/takayuki-nagata/hack_tools) (`has` assembler & `m2h` C-to-Hack transpiler)
  - Provides 16-bit C and Hack assembly firmware testing factorial, Fibonacci, array manipulations, and MMIO UART output on the Dual-ISA SoC.

---

## Key Features

- **Target Board**: [Sipeed Tang Nano 9K](https://wiki.sipeed.com/hardware/en/tang/Tang-Nano-9K/Nano-9K.html) (Gowin GW1NR-9 FPGA)
- **CPU Core**: Dual-ISA `unified_cpu` (ported to Veryl from [`hack_cpu`](https://github.com/takayuki-nagata/hack_cpu)) with auto-detection of **RISC-V RV32I 32-bit** and **Nand2Tetris Hack 16-bit** instruction sets.
- **Hardware Description**: 100% [Veryl](https://github.com/veryl-lang/veryl) (SystemVerilog output).
- **Firmware**: Bare-Metal Rust (`no_std` + `alloc` heap, `riscv32i-unknown-none-elf` target).
- **Peripherals**:
  - Full-Duplex UART Controller (`uart_controller`, ported from [`uart_controller`](https://github.com/takayuki-nagata/uart_controller)).
  - 64-bit RISC-V Machine Timer Core (`mtime` / `mtimecmp` for Zephyr RTOS tick compatibility).
  - SD Card SPI Master Controller (for bootloader & persistent ROM/storage access).
- **Verification & Simulation (OSS CAD Suite)**:
  - Fast [Icarus Verilog](https://steveicarus.github.io/iverilog/) + [Cocotb](https://www.cocotb.org/) RTL simulation suite.
  - Official **RISC-V Architectural Compliance Test Suite** (`riscv-arch-test`, 45/45 passing: 39 RV32I + 6 Zicsr).
  - Python behavioral SoC emulator (`sim/emulator.py`).
  - Automated CI via GitHub Actions (`.github/workflows/ci.yml`).
- **FPGA Synthesis**:
  - [Yosys](https://yosyshq.net/yosys/) `synth_gowin` automated synthesis for Tang Nano 9K.

---

## Memory Map (Harvard & MMIO)

| Address Range | Space / Peripheral | Description |
|:---|:---|:---|
| `0x0000_0000` - `0x0003_FFFF` | **Instruction ROM/RAM (256 KB)** | Code execution space (loaded from SD Card / HEX) |
| `0x2000_0000` - `0x2001_FFFF` | **Data RAM (128 KB)** | `.data`, `.bss`, stack, and `alloc` heap region |
| `0x4000_0000` - `0x4000_000F` | **UART Controller** | TX/RX data registers & FIFO status |
| `0x4000_1000` - `0x4000_101F` | **System Timer** | 64-bit `mtime` & `mtimecmp` registers |
| `0x4000_2000` - `0x4000_200F` | **SD Card SPI Controller** | SPI data, CS control, status, clock divisor |

---

## Directory Structure

```
VUX9K/
├── Makefile                        # Unified build, test, and synthesis automation
├── Veryl.lock
├── Veryl.toml                      # Veryl project configuration
├── README.md                       # Project documentation
├── LICENSE                         # MIT License
├── cpu/                            # Veryl CPU Core & ISA Modules (from hack_cpu)
│   ├── auto_mode_detector.veryl    # Dual-ISA mode auto-detection logic
│   ├── hack_translator.veryl       # Hack 16-bit to micro-op translator
│   ├── rv32i_alu.veryl             # RV32I / Hack shared ALU
│   ├── rv32i_csrs.veryl            # Machine-Mode CSRs & System Trap Unit
│   ├── rv32i_decode.veryl          # RV32I instruction decoder & imm generator
│   ├── rv32i_pkg.veryl             # Opcodes and ALU operation definitions
│   ├── rv32i_regfile.veryl         # Dual-write port 32-register register file
│   └── unified_cpu.veryl           # Unified Dual-ISA CPU Top Module
├── uart/                           # Veryl UART Controller & Peripherals (from uart_controller)
│   ├── clk_timer.veryl             # Baud rate pulse generator
│   ├── fifo_sync.veryl             # Synchronous FIFO buffer
│   ├── shift_registers.veryl       # Parallel load shift register
│   ├── uart_tx.veryl               # 8N1 UART Transmitter
│   ├── uart_rx.veryl               # 8N1 UART Receiver
│   └── uart_controller.veryl       # Integrated UART Controller with FIFOs
├── soc/                            # Veryl SoC Top Level & Interconnect
│   ├── timer_core.veryl            # 64-bit mtime/mtimecmp timer core
│   ├── sdcard_spi.veryl            # SD Card SPI master controller
│   ├── soc_ram.veryl               # Harvard 256KB I-RAM + 128KB D-RAM module
│   └── soc_top.veryl               # Tang Nano 9K SoC top-level wrapper
├── firmware/                       # Bare-metal Rust firmware crate
│   ├── Cargo.toml
│   ├── bootstrap/                  # Assembly entry point (start.s) & linker script (link.x)
│   └── src/                        # Rust drivers & main entry point
├── firmware_hack/                  # Hack 16-bit C and Assembly firmware
│   ├── Makefile                    # Hack toolchain build automation
│   └── src/                        # C math tests (main.c), MMIO UART (uart.c/h), assembly crt0 (main.asm)
├── vendor/                         # External submodules
│   ├── bc_clone_rs/                # Arbitrary-precision math engine & Zephyr app
│   └── riscv-arch-test/            # RISC-V architectural compliance test suite
├── zephyr_workspace/              # Out-of-Tree Zephyr RTOS & Rust integration
│   ├── boards/                     # Out-of-Tree Board definition (vux9k)
│   ├── dts/bindings/               # Custom DeviceTree YAML bindings (vux9k,uart)
│   ├── drivers/                    # Custom Out-of-Tree drivers (uart_vux9k)
│   ├── soc/                        # Out-of-Tree SoC definitions (soc.h, Kconfig)
│   └── app/                        # Zephyr C entry + Rust staticlib application
│       ├── CMakeLists.txt          # CMake Cargo integration
│       ├── prj.conf                # Zephyr Kconfig options
│       ├── src/main.c              # C kernel entry & Rust handover
│       └── rust_app/               # Rust staticlib crate (riscv32i-unknown-none-elf)
├── sim/                            # Simulation testbenches (Cocotb & SystemVerilog)
│   ├── Makefile                    # Cocotb / Icarus test runner Makefile
│   ├── emulator.py                 # Behavioral Python SoC emulator (fast MIPS, CLINT, UART FIFO)
│   ├── tb_hex_runner.sv            # Fast SystemVerilog compliance testbench
│   ├── test_rv32i_*.py             # CPU unit tests (ALU, Decode, Regfile, Compliance)
│   ├── test_hack_*.py              # Hack unit tests (Translator, Ops, Firmware)
│   ├── test_clk_timer.py           # UART Timer unit test
│   ├── test_shift_registers.py     # UART Shift register unit test
│   ├── test_fifo_sync.py           # UART FIFO unit test
│   ├── test_uart_*.py              # UART TX, RX, Controller unit tests
│   ├── test_soc_rv32i.py           # RISC-V 32-bit SoC integration test
│   ├── test_soc_hack.py            # Hack 16-bit SoC integration test
│   ├── test_soc_zephyr.py          # Zephyr RTOS & Rust SoC RTL integration test
│   └── test_soc_bc.py              # Zephyr bc_clone_rs self-tests & interactive REPL pytest suite
└── scripts/                        # Utility & Compliance scripts
    ├── bin2hex.py                  # Raw binary to Hex word converter (supports RISC-V 32-bit and Hack 16-bit)
    ├── elf2bin.py                  # ELF to raw binary extractor
    ├── check_no_absolute_paths.py  # Path validation script
    ├── link.ld                     # Linker script for architectural compliance
    ├── run_arch_test.py            # Official riscv-arch-test automation runner
    └── target_env/                 # Target environment headers for arch-tests
```

---

## Verification & Build Commands

### Prerequisites
1. **Rust Toolchain**:
   ```bash
   rustup target add riscv32i-unknown-none-elf
   ```
2. **OSS CAD Suite** (Yosys, Icarus Verilog):
   [YosysHQ/oss-cad-suite-build](https://github.com/YosysHQ/oss-cad-suite-build)
3. **Veryl**:
   ```bash
   cargo install veryl --version 0.20.3
   ```
4. **Python Environment with `uv`**:
   ```bash
   uv venv --python 3.13 .venv
   uv pip install cocotb pytest
   ```
5. **Hack Toolchain** (`has` assembler & `m2h` transpiler):
   ```bash
   make install-hack-tools
   ```
6. **Zephyr SDK & Toolchain** (for Zephyr RTOS / `bc_clone_rs` builds):
   - Zephyr SDK `0.16.8` with `riscv64-zephyr-elf` toolchain and `west`.

### Running Tests

```bash
# Clone / synchronize submodules (including vendor/bc_clone_rs)
make submodule-sync

# Run all module unit tests (ALU, Decoder, Regfile, Translator, UART modules)
make sim-unit

# Run official RISC-V Architectural Compliance Tests (riscv-arch-test 45/45: 39 RV32I + 6 Zicsr)
make test-arch-compliance

# Run SoC Integration Tests (Rust firmware & Hack binary on soc_top)
make sim-soc

# Build Hack 16-bit C / Assembly Firmware
make build-hack

# Run Hack Firmware Tests (Python SoC Emulator, Pytest suite, Cocotb RTL)
make sim-hack

# Build Zephyr RTOS bc_clone_rs math application
make build-zephyr

# Run Zephyr bc_clone_rs on Python SoC Emulator (10/10 math engine self-tests)
make sim-zephyr-emu

# Run automated Pytest suite for Zephyr bc_clone_rs self-tests & interactive REPL
make sim-zephyr-repl

# Run Full Zephyr RTOS & Rust Application Simulation (Python Emulator, REPL pytest, Cocotb RTL)
make sim-zephyr

# Run Full Verification Pipeline (Veryl check -> Firmware build -> Hack build -> Zephyr build -> All Tests -> Gowin Synthesis)
make test
```

---

## License

This project is dual-licensed:

- **Hardware RTL (Veryl/SV), Bare-Metal Firmware, Test Suites & Scripts**: [MIT License](LICENSE) (see [`LICENSES/MIT.txt`](LICENSES/MIT.txt))
- **Zephyr RTOS Out-of-Tree BSP & Application**: [Apache License 2.0](LICENSES/Apache-2.0.txt) (compliant with upstream Zephyr RTOS licensing)
- **External Submodules**:
  - `vendor/bc_clone_rs`: [MIT License](https://github.com/takayuki-nagata/bc_clone_rs/blob/main/LICENSE)
  - `vendor/riscv-arch-test`: [BSD 3-Clause License](https://github.com/riscv-non-isa/riscv-arch-test/blob/master/LICENSE)

All source files contain explicit [SPDX-License-Identifier](https://spdx.dev/ids/) tags and copyright notices compliant with the [REUSE](https://reuse.software/) specification.
