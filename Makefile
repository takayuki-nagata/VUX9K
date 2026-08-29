# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

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

export PATH := $(PWD)/$(VENV_PATH)/bin:$(OSS_CAD_SUITE_BIN):$(CARGO_BIN):$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH)

.PHONY: all veryl check check-paths fmt test test-ci test-hw test-hardware build synth synth-top pnr bitstream build-hw prog-sram prog-flash clean venv setup firmware sim-unit sim-boot sim-soc sim test-arch-compliance zephyr-rust-lib zephyr-bc-lib build-zephyr sim-zephyr-emu sim-zephyr-repl sim-zephyr-rtl sim-zephyr submodule-sync install-hack-tools build-hack sim-hack-emu sim-hack-pytest sim-hack-rtl sim-hack sim-hw-flow sim-gls sta

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

VERYL_SRCS = $(wildcard cpu/*.veryl) $(wildcard soc/*.veryl) $(wildcard uart/*.veryl) Veryl.toml

.veryl_build: $(VERYL_SRCS)
	$(VERYL) build
	@touch .veryl_build

veryl: .veryl_build

check-paths:
	$(PYTHON) scripts/check_no_absolute_paths.py

check: check-paths
	$(VERYL) check

fmt:
	$(VERYL) fmt

FIRMWARE_SRCS = $(wildcard firmware/src/*.rs) $(wildcard firmware/bootstrap/*) firmware/Cargo.toml firmware/Cargo.lock

firmware/firmware.hex: $(FIRMWARE_SRCS)
	cd firmware && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware firmware/firmware.bin
	$(PYTHON) scripts/bin2hex.py firmware/firmware.bin firmware/firmware.hex
	cp firmware/firmware.hex ./firmware.hex
	cp firmware/firmware.hex ./sim/firmware.hex 2>/dev/null || true
	cp firmware_d*.hex ./sim/ 2>/dev/null || true

firmware: firmware/firmware.hex

BC_APP_DIR ?= vendor/bc_clone_rs/examples/zephyr_app
ZEPHYR_BUILD_DIR ?= build_zephyr

submodule-sync:
	git submodule update --init --recursive

zephyr-rust-lib:
	cd zephyr_workspace/app/rust_app && $(CARGO) build --release --target riscv32i-unknown-none-elf

zephyr-bc-lib:
	cd vendor/bc_clone_rs/crates/bc_zephyr && $(CARGO) build --release --target riscv32i-unknown-none-elf --no-default-features

build-zephyr:
	@if [ -d "$(ZEPHYR_BASE)" ] && [ -d "$(ZEPHYR_SDK_INSTALL_DIR)" ]; then \
		echo "=== Building Zephyr bc_clone_rs Application (vux9k) ==="; \
		export PATH=$(PWD)/$(VENV_PATH)/bin:$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH) && \
		export ZEPHYR_BASE=$(ZEPHYR_BASE) && \
		export ZEPHYR_SDK_INSTALL_DIR=$(ZEPHYR_SDK_INSTALL_DIR) && \
		export ZEPHYR_TOOLCHAIN_VARIANT=zephyr && \
		west build -p auto -b vux9k $(BC_APP_DIR) -d $(ZEPHYR_BUILD_DIR) -- \
			-DBOARD_ROOT=$(PWD)/zephyr_workspace \
			-DSOC_ROOT=$(PWD)/zephyr_workspace \
			-DEXTRA_ZEPHYR_MODULES=$(PWD)/zephyr_workspace \
			-DRUST_TARGET=riscv32i-unknown-none-elf && \
		$(PYTHON) scripts/elf2bin.py $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.elf $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin; \
	else \
		echo "Zephyr or Zephyr SDK not found. Skipping real Zephyr build."; \
	fi

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

test-arch-compliance: veryl
	@echo "=== Running Official RISC-V Architectural Compliance Tests ==="
	$(PYTHON) scripts/run_arch_test.py

sim-boot: veryl
	@echo "=== Running Hardware Boot Manager & Bridge Cocotb Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_boot

sim-soc: firmware sim-boot
	@echo "=== Running Python Software Emulator Unit Tests ==="
	$(PYTHON) -m pytest sim/test_emulator.py
	@echo "=== Running Python Software Emulator ==="
	$(PYTHON) sim/emulator.py firmware/firmware.bin
	@echo "=== Running SoC Top RISC-V Integration Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_rv32i
	@echo "=== Running SoC Top Hack Integration Tests ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hack

sim-zephyr-emu: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Python Emulator ==="
	@if [ -f "$(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin" ]; then \
		$(PYTHON) sim/emulator.py $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin --steps 120000000 --until "bc> "; \
	else \
		$(PYTHON) sim/emulator.py firmware/firmware.bin; \
	fi

sim-zephyr-repl: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Self-Tests & REPL Pytest Suite ==="
	$(PYTHON) -m pytest sim/test_soc_bc.py

sim-zephyr-rtl: zephyr-bc-lib
	@echo "=== Running Zephyr/Rust SoC RTL Simulation ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_zephyr

sim-zephyr: sim-zephyr-emu sim-zephyr-repl sim-zephyr-rtl

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

sim-hack-emu: build-hack
	@echo "=== Running Hack Firmware on Python SoC Emulator ==="
	$(PYTHON) sim/emulator.py build_hack/firmware.bin

sim-hack-pytest: build-hack
	@echo "=== Running Hack Firmware Pytest Test Suite ==="
	$(PYTHON) -m pytest sim/test_hack_firmware.py

sim-hack-rtl: build-hack
	@echo "=== Running Hack 16-bit SoC RTL Simulation ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hack

sim-hack: sim-hack-emu sim-hack-pytest sim-hack-rtl

synth-units: veryl
	@echo "=== Synthesizing Submodules to Gowin Netlists for GLS Unit Tests ==="
	$(YOSYS) -p "read_verilog -sv cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv cpu/hack_translator.sv cpu/rv32i_alu.sv cpu/rv32i_decode.sv cpu/rv32i_regfile.sv cpu/rv32i_csrs.sv cpu/unified_cpu.sv; synth_gowin -top unified_cpu; write_verilog -noattr unified_cpu_syn.v"
	$(YOSYS) -p "read_verilog -sv uart/clk_timer.sv uart/shift_registers.sv uart/fifo_sync.sv uart/uart_tx.sv uart/uart_rx.sv uart/uart_controller.sv; synth_gowin -top uart_controller; write_verilog -noattr uart_controller_syn.v"
	$(YOSYS) -p "read_verilog -sv cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv; synth_gowin -top auto_mode_detector; write_verilog -noattr auto_mode_detector_syn.v"

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

sim-hw-flow: firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (RTL Simulation) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hardware_flow

sim-gls-hw-flow: synth-top firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (GLS Simulation) ==="
	$(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hardware_flow SIM_GLS=1

sim: sim-unit test-arch-compliance sim-gls-unit sim-soc-fast

synth: veryl
	$(YOSYS) -p "\
		read_verilog -sv cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv cpu/hack_translator.sv cpu/rv32i_alu.sv cpu/rv32i_decode.sv cpu/rv32i_regfile.sv cpu/rv32i_csrs.sv cpu/unified_cpu.sv uart/*.sv soc/timer_core.sv soc/sdcard_spi.sv soc/hw_boot_mgr.sv; \
		synth_gowin -top hw_boot_mgr -json hw_boot.json; \
	"

SOC_RTL_SRCS = cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv cpu/hack_translator.sv \
               cpu/rv32i_alu.sv cpu/rv32i_decode.sv cpu/rv32i_regfile.sv cpu/rv32i_csrs.sv \
               cpu/unified_cpu.sv uart/clk_timer.sv uart/fifo_sync.sv uart/shift_registers.sv \
               uart/uart_tx.sv uart/uart_rx.sv uart/uart_controller.sv soc/timer_core.sv \
               soc/sdcard_spi.sv soc/gpio_controller.sv soc/hw_boot_mgr.sv soc/soc_ram.sv soc/soc_top.sv

soc.json soc_syn.v: .veryl_build firmware/firmware.hex $(SOC_RTL_SRCS)
	$(YOSYS) -p "\
		read_verilog -sv $(SOC_RTL_SRCS); \
		synth_gowin -top soc_top -json soc.json; \
		write_verilog -noattr soc_syn.v; \
	"

synth-top: soc.json

sim-gls: sim-gls-unit sim-soc-gls-fast

soc_pnr.json soc_sta.json: soc.json $(CST_FILE)
	$(NEXTPNR) --device GW1NR-LV9QN88PC6/I5 --vopt family=GW1N-9C --vopt cst=$(CST_FILE) --json soc.json --write soc_pnr.json --report soc_sta.json --detailed-timing-report --freq 27.0 --timing-allow-fail

pnr: soc_pnr.json

sta: soc_sta.json
	@echo "=== Generating Static Timing Analysis (STA) Report ==="
	$(PYTHON) scripts/report_sta.py soc_sta.json --strict

pack.fs: soc_pnr.json
	$(GOWIN_PACK) -d GW1N-9C -o pack.fs soc_pnr.json

bitstream: pack.fs

build-hw: pack.fs
	@echo "=== Hardware Bitstream pack.fs Built Successfully! ==="

prog-sram: pack.fs
	$(OPENFPGALOADER) -b tangnano9k pack.fs

prog-flash: pack.fs
	$(OPENFPGALOADER) -b tangnano9k -f pack.fs
	$(OPENFPGALOADER) -b tangnano9k pack.fs

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

test-hw: firmware build-hack build-hw prog-sram
	@echo "=== Running Automated End-to-End Hardware Test Suite on Tang Nano 9K ==="
	$(PYTHON) scripts/test_hardware.py
	@echo "========================================================================"
	@echo "  [HW] ALL REAL TANG NANO 9K HARDWARE & SD CARD TESTS PASSED 100%!     "
	@echo "========================================================================"

clean:
	$(VERYL) clean
	cd firmware && $(CARGO) clean
	cd zephyr_workspace/app/rust_app && $(CARGO) clean
	cd vendor/bc_clone_rs/crates/bc_zephyr 2>/dev/null && $(CARGO) clean || true
	$(MAKE) -C firmware_hack clean
	rm -rf sim/sim_build* sim/results.xml *.json *_syn.v sim/*_syn.v pack.fs firmware/firmware.bin firmware/firmware.hex build_arch_test build_hack zephyr_workspace/app/build $(ZEPHYR_BUILD_DIR) .veryl_build
