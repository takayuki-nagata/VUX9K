# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# ===== Toolchain Variables & Setup =====

VERYL = veryl
YOSYS = yosys
GOWIN_PACK = gowin_pack
NEXTPNR ?= nextpnr-himbaechel
OPENFPGALOADER ?= openFPGALoader
CST_FILE ?= tangnano9k.cst
CARGO = cargo
PYTHON = python3
UV = uv

VENV_PATH ?= .venv
ZEPHYR_BASE ?= $(HOME)/zephyrproject/zephyr
ZEPHYR_SDK_INSTALL_DIR ?= $(HOME)/.local/zephyr-sdk-0.16.8
OSS_CAD_SUITE_BIN ?= $(HOME)/.local/oss-cad-suite/bin
CARGO_BIN ?= $(HOME)/.cargo/bin

export PATH := $(CURDIR)/$(VENV_PATH)/bin:$(OSS_CAD_SUITE_BIN):$(CARGO_BIN):$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH)

# Unified build output tree: every generated/build artifact lives under $(BUILD_DIR),
# keeping source directories (cpu/, soc/, uart/, firmware/, ...) generated-file-free.
BUILD_DIR := build
VERYL_OUT_DIR := $(BUILD_DIR)/veryl
FIRMWARE_BUILD_DIR := $(BUILD_DIR)/firmware
HACK_BUILD_DIR := $(BUILD_DIR)/hack
ZEPHYR_BUILD_DIR ?= $(BUILD_DIR)/zephyr
SYNTH_DIR := $(BUILD_DIR)/synth

.PHONY: all veryl check check-paths fmt test test-ci test-hw test-hardware build synth-top pnr bitstream build-hw prog-sram prog-flash clean venv setup firmware sim-unit sim-boot sim-soc sim test-arch-compliance zephyr-rust-lib zephyr-bc-lib build-zephyr sim-zephyr-emu sim-zephyr-repl sim-zephyr-rtl sim-zephyr submodule-sync install-hack-tools build-hack sim-hack-emu sim-hack-pytest sim-hack-rtl sim-hack sim-hw-flow sim-gls sta

all: test-ci

venv: $(VENV_PATH)/bin/activate

$(VENV_PATH)/bin/activate:
	@if [ ! -d "$(VENV_PATH)" ]; then \
		echo "Creating virtual environment using uv..."; \
		$(UV) venv $(VENV_PATH) --python 3.13; \
	fi

setup: venv
	$(UV) pip install --python $(VENV_PATH)/bin/python cocotb pytest pyserial
	@if [ -d ".git" ]; then \
		echo "Configuring Git core.hooksPath to .githooks..."; \
		git config core.hooksPath .githooks; \
	fi

check-paths:
	$(PYTHON) scripts/check_no_absolute_paths.py

check: check-paths
	$(VERYL) check

fmt:
	$(VERYL) fmt

# ===== Veryl Build =====

VERYL_SRCS = $(wildcard cpu/*.veryl) $(wildcard soc/*.veryl) $(wildcard uart/*.veryl) Veryl.toml

$(VERYL_OUT_DIR)/.stamp: $(VERYL_SRCS)
	@mkdir -p $(VERYL_OUT_DIR)
	$(VERYL) build --out-dir $(VERYL_OUT_DIR)
	@touch $(VERYL_OUT_DIR)/.stamp

veryl: $(VERYL_OUT_DIR)/.stamp

# ===== Firmware (Rust Boot Manager & Resident Loader) =====

FIRMWARE_SRCS = $(wildcard firmware/src/*.rs) $(wildcard firmware/bootstrap/*) firmware/Cargo.toml firmware/Cargo.lock
LOADER_SRCS = $(wildcard resident_loader/src/*.rs) resident_loader/link.x resident_loader/Cargo.toml

$(FIRMWARE_BUILD_DIR)/firmware.hex: $(FIRMWARE_SRCS) $(LOADER_SRCS)
	@mkdir -p $(FIRMWARE_BUILD_DIR)
	cd resident_loader && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py resident_loader/target/riscv32i-unknown-none-elf/release/resident_loader $(FIRMWARE_BUILD_DIR)/resident_loader.bin
	cd firmware && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware $(FIRMWARE_BUILD_DIR)/firmware.bin $(FIRMWARE_BUILD_DIR)
	$(PYTHON) scripts/merge_firmware_hex.py $(FIRMWARE_BUILD_DIR)/firmware.bin $(FIRMWARE_BUILD_DIR)/resident_loader.bin $(FIRMWARE_BUILD_DIR)/firmware.hex
	@# soc_ram.veryl's $$readmemh() resolves these bare filenames relative to each tool's own
	@# process cwd (yosys/nextpnr: repo root, cocotb/icarus: sim/), so a copy must exist in each
	@# location; symlinks (not cp) keep them structurally identical to the canonical build/firmware/ copy.
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware.hex ./firmware.hex
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d0.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d1.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d2.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d3.hex .
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware.hex ./sim/firmware.hex 2>/dev/null || true
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d0.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d1.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d2.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d3.hex ./sim/ 2>/dev/null || true

firmware: $(FIRMWARE_BUILD_DIR)/firmware.hex

# ===== Zephyr (bc_clone_rs Out-of-Tree App) =====

BC_APP_DIR ?= vendor/bc_clone_rs/examples/zephyr_app

submodule-sync:
	git submodule update --init --recursive

zephyr-rust-lib:
	cd zephyr_workspace/app/rust_app && $(CARGO) build --release --target riscv32i-unknown-none-elf
	$(PYTHON) scripts/elf2bin.py zephyr_workspace/app/rust_app/target/riscv32i-unknown-none-elf/release/standalone zephyr_workspace/app/rust_app/app.bin

zephyr-bc-lib:
	cd vendor/bc_clone_rs/crates/bc_zephyr && $(CARGO) build --release --target riscv32i-unknown-none-elf --no-default-features

build-zephyr:
	@if [ -d "$(ZEPHYR_BASE)" ] && [ -d "$(ZEPHYR_SDK_INSTALL_DIR)" ]; then \
		echo "=== Building Zephyr bc_clone_rs Application (vux9k) ==="; \
		export PATH=$(CURDIR)/$(VENV_PATH)/bin:$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH) && \
		export ZEPHYR_BASE=$(ZEPHYR_BASE) && \
		export ZEPHYR_SDK_INSTALL_DIR=$(ZEPHYR_SDK_INSTALL_DIR) && \
		export ZEPHYR_TOOLCHAIN_VARIANT=zephyr && \
		west build -p auto -b vux9k $(BC_APP_DIR) -d $(ZEPHYR_BUILD_DIR) -- \
			-DBOARD_ROOT=$(CURDIR)/zephyr_workspace \
			-DSOC_ROOT=$(CURDIR)/zephyr_workspace \
			-DEXTRA_ZEPHYR_MODULES=$(CURDIR)/zephyr_workspace \
			-DRUST_TARGET=riscv32i-unknown-none-elf && \
		$(PYTHON) scripts/elf2bin.py $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.elf $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin; \
	else \
		echo "Zephyr or Zephyr SDK not found. Skipping real Zephyr build."; \
	fi

# ===== Hack 16-bit Toolchain & Firmware =====

MSP430_GCC_URL ?= https://dr-download.ti.com/software-development/ide-configuration-compiler-or-debugger/MD-LlCjWuAbzH/9.3.1.2/msp430-gcc-9.3.1.11_linux64.tar.bz2
LOCAL_MSP430_DIR ?= $(HOME)/.local/msp430-gcc

install-hack-tools:
	@echo "=== Installing Hack Toolchain (has, m2h & msp430-gcc) ==="
	@mkdir -p $(HOME)/.local/bin
	@if [ ! -f "$(HOME)/.local/bin/has" ]; then \
		TMP_DIR=$$(mktemp -d); \
		curl -sL https://github.com/takayuki-nagata/hack_tools/releases/download/v0.2.0/has-v0.2.0-linux-x86_64.tar.gz | tar -xz -C "$$TMP_DIR" && \
		cp "$$TMP_DIR"/has*/has $(HOME)/.local/bin/has && \
		chmod +x $(HOME)/.local/bin/has && \
		rm -rf "$$TMP_DIR"; \
	fi
	@if ! which msp430-gcc >/dev/null 2>&1 && ! which msp430-elf-gcc >/dev/null 2>&1 && [ ! -f "$(HOME)/.local/bin/msp430-gcc" ]; then \
		echo "Installing MSP430 GCC toolchain to $(LOCAL_MSP430_DIR)..."; \
		mkdir -p $(LOCAL_MSP430_DIR); \
		curl -fsSL $(MSP430_GCC_URL) | tar -xjf - -C $(LOCAL_MSP430_DIR) --strip-components=1 && \
		ln -sf $(LOCAL_MSP430_DIR)/bin/msp430-elf-gcc $(HOME)/.local/bin/msp430-gcc && \
		ln -sf $(LOCAL_MSP430_DIR)/bin/msp430-elf-gcc $(HOME)/.local/bin/msp430-elf-gcc; \
	fi
	$(UV) pip install --python $(VENV_PATH)/bin/python https://github.com/takayuki-nagata/hack_tools/releases/download/v0.2.0/m2h-0.2.0.tar.gz

build-hack:
	@echo "=== Building Hack 16-bit C/Asm Firmware ==="
	$(MAKE) -C firmware_hack

# ===== Simulation - Unit Tests =====

sim-unit: veryl
	@echo "=== Running RV32I ALU Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=rv32i_alu MODULE=test_rv32i_alu
	@echo "=== Running RV32I Decoder Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=rv32i_decode MODULE=test_rv32i_decode
	@echo "=== Running Hack Translator Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=hack_translator MODULE=test_hack_translator
	@echo "=== Running RV32I Register File Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=rv32i_regfile MODULE=test_rv32i_regfile
	@echo "=== Running RV32I CSRs & Trap Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=rv32i_csrs MODULE=test_rv32i_csrs
	@echo "=== Running Auto Mode Detector Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=auto_mode_detector MODULE=test_auto_mode_detector
	@echo "=== Running Unified Dual-ISA CPU Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_unified_cpu
	@echo "=== Running Hack CPU Comprehensive Ops Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_hack_cpu_ops
	@echo "=== Running RV32I ISA Compliance Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_rv32i_compliance
	@echo "=== Running UART Clock Timer Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=clk_timer MODULE=test_clk_timer
	@echo "=== Running UART Shift Registers Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=shift_registers MODULE=test_shift_registers
	@echo "=== Running UART FIFO Sync Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=fifo_sync MODULE=test_fifo_sync
	@echo "=== Running UART TX Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=uart_tx MODULE=test_uart_tx
	@echo "=== Running UART RX Unit Tests ==="
	$(MAKE) -C sim TOPLEVEL=uart_rx MODULE=test_uart_rx
	@echo "=== Running UART Controller Loopback Tests ==="
	$(MAKE) -C sim TOPLEVEL=uart_controller MODULE=test_uart_controller

# ===== Simulation - Arch Compliance =====

test-arch-compliance: veryl
	@echo "=== Running Official RISC-V Architectural Compliance Tests ==="
	$(PYTHON) scripts/run_arch_test.py

# ===== Simulation - SoC Integration =====

sim-boot: veryl
	@echo "=== Running Hardware Boot Manager & Bridge Cocotb Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_boot

sim-soc: firmware sim-boot
	@echo "=== Running Python Software Emulator Unit Tests ==="
	$(PYTHON) -m pytest sim/test_emulator.py
	@echo "=== Running Python Software Emulator ==="
	$(PYTHON) sim/emulator.py $(FIRMWARE_BUILD_DIR)/firmware.bin
	@echo "=== Running SoC Top RISC-V Integration Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_rv32i
	@echo "=== Running SoC Top Hack Integration Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hack

sim-zephyr-emu: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Python Emulator ==="
	@if [ -f "$(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin" ]; then \
		$(PYTHON) sim/emulator.py $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin --steps 120000000 --until "bc> "; \
	else \
		$(PYTHON) sim/emulator.py $(FIRMWARE_BUILD_DIR)/firmware.bin; \
	fi

sim-zephyr-repl: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Self-Tests & REPL Pytest Suite ==="
	$(PYTHON) -m pytest sim/test_soc_bc.py

sim-zephyr-rtl: zephyr-bc-lib
	@echo "=== Running Zephyr/Rust SoC RTL Simulation ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_zephyr

sim-zephyr: sim-zephyr-emu sim-zephyr-repl sim-zephyr-rtl

sim-hack-emu: build-hack
	@echo "=== Running Hack Firmware on Python SoC Emulator ==="
	$(PYTHON) sim/emulator.py $(HACK_BUILD_DIR)/firmware.bin

sim-hack-pytest: build-hack
	@echo "=== Running Hack Firmware Pytest Test Suite ==="
	$(PYTHON) -m pytest sim/test_hack_firmware.py

sim-hack-rtl: build-hack
	@echo "=== Running Hack 16-bit SoC RTL Simulation ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hack

sim-hack: sim-hack-emu sim-hack-pytest sim-hack-rtl

sim-hw-flow: firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (RTL Simulation) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hardware_flow

sim-gls-hw-flow: synth-top firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (GLS Simulation) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hardware_flow SIM_GLS=1

sim: sim-unit test-arch-compliance sim-gls-unit sim-soc-fast

# ===== Synthesis / PnR / STA / Bitstream / Programming =====

synth-units: veryl
	@echo "=== Synthesizing Submodules to Gowin Netlists for GLS Unit Tests ==="
	@mkdir -p $(SYNTH_DIR)
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/cpu/auto_mode_detector.sv $(VERYL_OUT_DIR)/cpu/hack_translator.sv $(VERYL_OUT_DIR)/cpu/rv32i_alu.sv $(VERYL_OUT_DIR)/cpu/rv32i_decode.sv $(VERYL_OUT_DIR)/cpu/rv32i_regfile.sv $(VERYL_OUT_DIR)/cpu/rv32i_csrs.sv $(VERYL_OUT_DIR)/cpu/unified_cpu.sv; synth_gowin -top unified_cpu; write_verilog -noattr $(SYNTH_DIR)/unified_cpu_syn.v"
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/uart/clk_timer.sv $(VERYL_OUT_DIR)/uart/shift_registers.sv $(VERYL_OUT_DIR)/uart/fifo_sync.sv $(VERYL_OUT_DIR)/uart/uart_tx.sv $(VERYL_OUT_DIR)/uart/uart_rx.sv $(VERYL_OUT_DIR)/uart/uart_controller.sv; synth_gowin -top uart_controller; write_verilog -noattr $(SYNTH_DIR)/uart_controller_syn.v"
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/cpu/auto_mode_detector.sv; synth_gowin -top auto_mode_detector; write_verilog -noattr $(SYNTH_DIR)/auto_mode_detector_syn.v"

sim-gls-unit: synth-units
	@echo "=== Running Gowin Primitive GLS: Unified CPU Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_unified_cpu SIM_GLS=1
	@echo "=== Running Gowin Primitive GLS: Hack CPU Ops Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_hack_cpu_ops SIM_GLS=1
	@echo "=== Running Gowin Primitive GLS: RV32I ISA Compliance Tests ==="
	$(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_rv32i_compliance SIM_GLS=1
	@echo "=== Running Gowin Primitive GLS: UART Controller Loopback Tests ==="
	$(MAKE) -C sim TOPLEVEL=uart_controller MODULE=test_uart_controller SIM_GLS=1
	@echo "=== Running Gowin Primitive GLS: Auto Mode Detector Tests ==="
	$(MAKE) -C sim TOPLEVEL=auto_mode_detector MODULE=test_auto_mode_detector SIM_GLS=1

sim-soc-fast: veryl firmware
	@echo "=== Running Fast SoC Top Boot & Execution Verification (RTL) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_fast

sim-soc-gls-fast: synth-top firmware
	@echo "=== Running Fast SoC Top Boot & Execution Verification (GLS Netlist) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_gls_fast SIM_GLS=1

SOC_RTL_SRCS = $(VERYL_OUT_DIR)/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/cpu/auto_mode_detector.sv $(VERYL_OUT_DIR)/cpu/hack_translator.sv \
               $(VERYL_OUT_DIR)/cpu/rv32i_alu.sv $(VERYL_OUT_DIR)/cpu/rv32i_decode.sv $(VERYL_OUT_DIR)/cpu/rv32i_regfile.sv $(VERYL_OUT_DIR)/cpu/rv32i_csrs.sv \
               $(VERYL_OUT_DIR)/cpu/unified_cpu.sv $(VERYL_OUT_DIR)/uart/clk_timer.sv $(VERYL_OUT_DIR)/uart/fifo_sync.sv $(VERYL_OUT_DIR)/uart/shift_registers.sv \
               $(VERYL_OUT_DIR)/uart/uart_tx.sv $(VERYL_OUT_DIR)/uart/uart_rx.sv $(VERYL_OUT_DIR)/uart/uart_controller.sv $(VERYL_OUT_DIR)/soc/timer_core.sv \
               $(VERYL_OUT_DIR)/soc/sdcard_spi.sv $(VERYL_OUT_DIR)/soc/gpio_controller.sv $(VERYL_OUT_DIR)/soc/soc_ram.sv $(VERYL_OUT_DIR)/soc/soc_top.sv

$(SYNTH_DIR)/soc.json $(SYNTH_DIR)/soc_syn.v: $(VERYL_OUT_DIR)/.stamp $(FIRMWARE_BUILD_DIR)/firmware.hex $(SOC_RTL_SRCS)
	@mkdir -p $(SYNTH_DIR)
	$(YOSYS) -p "\
		read_verilog -sv $(SOC_RTL_SRCS); \
		synth_gowin -top soc_top -json $(SYNTH_DIR)/soc.json; \
		write_verilog -noattr $(SYNTH_DIR)/soc_syn.v; \
	"

synth-top: $(SYNTH_DIR)/soc.json

sim-gls: sim-gls-unit sim-soc-gls-fast

PNR_SEEDS ?= 100 1 42 7 13

$(SYNTH_DIR)/soc_pnr.json $(SYNTH_DIR)/soc_sta.json: $(SYNTH_DIR)/soc.json $(CST_FILE)
	$(PYTHON) scripts/run_pnr.py --device GW1NR-LV9QN88PC6/I5 --vopt family=GW1N-9C --vopt cst=$(CST_FILE) --json $(SYNTH_DIR)/soc.json --write $(SYNTH_DIR)/soc_pnr.json --report $(SYNTH_DIR)/soc_sta.json --freq 30.0 --seeds $(PNR_SEEDS)

pnr: $(SYNTH_DIR)/soc_pnr.json

sta: $(SYNTH_DIR)/soc_sta.json
	@echo "=== Generating Static Timing Analysis (STA) Report (Target: 30.0 MHz, 10% Safety Margin) ==="
	$(PYTHON) scripts/report_sta.py $(SYNTH_DIR)/soc_sta.json --freq 30.0 --strict

$(SYNTH_DIR)/pack.fs: $(SYNTH_DIR)/soc_pnr.json
	$(GOWIN_PACK) -d GW1N-9C -o $(SYNTH_DIR)/pack.fs $(SYNTH_DIR)/soc_pnr.json

bitstream: $(SYNTH_DIR)/pack.fs

build-hw: $(SYNTH_DIR)/pack.fs
	@echo "=== Hardware Bitstream pack.fs Built Successfully! ==="

prog-sram: $(SYNTH_DIR)/pack.fs
	$(OPENFPGALOADER) -b tangnano9k $(SYNTH_DIR)/pack.fs

prog-flash: $(SYNTH_DIR)/pack.fs
	$(OPENFPGALOADER) -b tangnano9k -f $(SYNTH_DIR)/pack.fs

# ===== Aggregate Test Targets =====

test-sim: check firmware zephyr-rust-lib zephyr-bc-lib build-hack build-zephyr sim-unit test-arch-compliance sim-gls-unit sim-soc-fast synth-top sim-soc-gls-fast
	@echo "========================================================================"
	@echo "  [SIM] ALL RTL, GLS NETLIST, COMPLIANCE & SOC SIMULATION TESTS PASSED! "
	@echo "========================================================================"

test: test-sim sta
	@echo "========================================================================"
	@echo "  [TEST] ALL SIMULATION & STATIC TIMING ANALYSIS (STA) PASSED 100%!     "
	@echo "========================================================================"

test-ci: test

test-hardware: test-hw

test-hw: zephyr-rust-lib firmware build-hack build-hw prog-sram
	@echo "=== Running Automated End-to-End Hardware Test Suite on Tang Nano 9K ==="
	$(PYTHON) scripts/test_hardware.py
	@echo "========================================================================"
	@echo "  [HW] ALL REAL TANG NANO 9K HARDWARE & SD CARD TESTS PASSED 100%!     "
	@echo "========================================================================"

# ===== Clean =====

clean:
	$(VERYL) clean
	cd firmware && $(CARGO) clean
	cd zephyr_workspace/app/rust_app && $(CARGO) clean
	cd vendor/bc_clone_rs/crates/bc_zephyr 2>/dev/null && $(CARGO) clean || true
	$(MAKE) -C firmware_hack clean
	rm -rf $(BUILD_DIR) ./firmware.hex ./firmware_d*.hex ./sim/firmware.hex ./sim/firmware_d*.hex zephyr_workspace/app/build
