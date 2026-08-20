# VUX9K

**VUX9K** (Veryl Unified eXecution on Tang Nano 9K) is a **Dual-ISA (RISC-V RV32I & Hack 16-bit) System-on-Chip (SoC)** design targeting the **Sipeed Tang Nano 9K** FPGA board. 

The SoC is designed with future **Zephyr RTOS** and **Rust application execution** in mind, featuring a Harvard Architecture with SD-Card-backed instruction loading, MMIO UART, System Timer (`mtime`/`mtimecmp`), and dual-mode CPU instruction execution.

---

## Key Features

- **Target Board**: [Sipeed Tang Nano 9K](https://wiki.sipeed.com/hardware/en/tang/Tang-Nano-9K/Nano-9K.html) (Gowin GW1NR-9 FPGA)
- **CPU Core**: [`hack_cpu`](https://github.com/takayuki-nagata/hack_cpu) `unified_cpu` (VHDL-2008) with auto-detection of **RISC-V RV32I 32-bit** and **Nand2Tetris Hack 16-bit** instruction sets.
- **Hardware Languages**: [Veryl](https://github.com/veryl-lang/veryl) & VHDL-2008.
- **Firmware**: Bare-Metal Rust (`no_std` + `alloc` heap, `riscv32i-unknown-none-elf` target).
- **Peripherals**:
  - Full-Duplex UART Controller ([`uart_controller`](https://github.com/takayuki-nagata/uart_controller)).
  - 64-bit RISC-V Machine Timer Core (`mtime` / `mtimecmp` for Zephyr RTOS tick compatibility).
  - SD Card SPI Master Controller (for bootloader & persistent ROM/storage access).
- **Verification & Simulation**:
  - GHDL + [cocotb](https://www.cocotb.org/) RTL simulation suite.
  - Python behavioral SoC emulator (`sim/emulator.py`).
  - Automated CI via GitHub Actions (`.github/workflows/ci.yml`).

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
├── Makefile                        # Top-level build & test automation
├── README.md                       # Project documentation
├── LICENSE                         # MIT License
├── .gitmodules                     # Submodule definitions
├── submodules/
│   ├── hack_cpu/                   # Git Submodule: Dual-ISA VHDL CPU Core
│   └── uart_controller/            # Git Submodule: VHDL UART Controller Core
├── soc/                            # Veryl / VHDL SoC hardware top level & peripherals
│   ├── soc_top.vhd                 # Top-level VHDL-2008 SoC wrapper
│   ├── soc_ram.vhd                 # Harvard 256KB I-RAM + 128KB D-RAM module
│   ├── timer_core.vhd / .veryl     # 64-bit mtime/mtimecmp timer core
│   └── sdcard_spi.vhd / .veryl     # SD Card SPI master controller
├── firmware/                       # Bare-metal Rust firmware crate
│   ├── Cargo.toml
│   ├── bootstrap/                  # Assembly entry point (start.s) & linker script (link.x)
│   └── src/                        # Rust drivers & main entry point
├── sim/                            # cocotb & Python Emulator testbenches
│   ├── Makefile                    # cocotb Makefile (GHDL runner)
│   ├── emulator.py                 # Behavioral Python SoC emulator
│   ├── test_soc_rv32i.py           # RISC-V 32-bit cocotb simulation test
│   └── test_soc_hack.py            # Hack 16-bit cocotb simulation test
└── scripts/                        # Utility scripts (elf2bin.py, bin2hex.py)
```

---

## Getting Started & Verification

### Prerequisites
1. **Rust Toolchain**:
   ```bash
   rustup target add riscv32i-unknown-none-elf
   ```
2. **Python Environment with `uv`**:
   ```bash
   uv venv .venv
   uv pip install cocotb pytest
   ```
3. **GHDL Simulator**:
   ```bash
   sudo apt-get install ghdl
   ```

### Running Tests
To run the full automated verification pipeline (Firmware build -> Behavioral Emulator -> cocotb GHDL simulation):

```bash
make test
```

---

## License

This project is licensed under the [MIT License](LICENSE).
