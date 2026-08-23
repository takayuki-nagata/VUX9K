# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

VERYL = veryl
YOSYS = yosys
GOWIN_PACK = gowin_pack
NEXTPNR = nextpnr-gowin
CARGO = cargo
PYTHON = python3
UV = uv

VENV_PATH ?= .venv
ZEPHYR_BASE ?= $(HOME)/zephyrproject/zephyr
ZEPHYR_SDK_INSTALL_DIR ?= $(HOME)/.local/zephyr-sdk-0.16.8
OSS_CAD_SUITE_BIN ?= $(HOME)/.local/oss-cad-suite/bin
CARGO_BIN ?= $(HOME)/.cargo/bin

export PATH := $(PWD)/$(VENV_PATH)/bin:$(OSS_CAD_SUITE_BIN):$(CARGO_BIN):$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH)

.PHONY: all veryl check check-paths fmt test build synth clean venv setup firmware sim-unit sim-soc sim test-arch-compliance zephyr-rust-lib zephyr-bc-lib build-zephyr sim-zephyr-emu sim-zephyr-repl sim-zephyr-rtl sim-zephyr submodule-sync

all: test

venv: $(VENV_PATH)/bin/activate

$(VENV_PATH)/bin/activate:
	@if [ ! -d "$(VENV_PATH)" ]; then \
		echo "Creating virtual environment using uv..."; \
		$(UV) venv $(VENV_PATH) --python 3.13; \
	fi

setup: venv
	$(UV) pip install --python $(VENV_PATH)/bin/python cocotb pytest
	@if [ -d ".git" ]; then \
		echo "Configuring Git core.hooksPath to .githooks..."; \
		git config core.hooksPath .githooks; \
	fi

veryl:
	$(VERYL) build

check-paths:
	$(PYTHON) scripts/check_no_absolute_paths.py

check: check-paths
	$(VERYL) check

fmt:
	$(VERYL) fmt

firmware:
	cd firmware && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware firmware/firmware.bin
	$(PYTHON) scripts/bin2hex.py firmware/firmware.bin firmware/firmware.hex

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

sim-soc: firmware
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

sim: sim-unit test-arch-compliance sim-soc sim-zephyr

synth: veryl
	$(YOSYS) -p "\
		read_verilog -sv cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv cpu/hack_translator.sv cpu/rv32i_alu.sv cpu/rv32i_decode.sv cpu/rv32i_regfile.sv cpu/rv32i_csrs.sv cpu/unified_cpu.sv uart/*.sv soc/*.sv; \
		synth_gowin -top soc_top -json soc.json; \
	"

test: check firmware zephyr-rust-lib zephyr-bc-lib sim synth
	@echo "========================================================================"
	@echo "  ALL VERYL CPU, UART, ARCH-COMPLIANCE & SOC TESTS PASSED 100%!         "
	@echo "========================================================================"

clean:
	$(VERYL) clean
	cd firmware && $(CARGO) clean
	cd zephyr_workspace/app/rust_app && $(CARGO) clean
	cd vendor/bc_clone_rs/crates/bc_zephyr 2>/dev/null && $(CARGO) clean || true
	rm -rf sim/sim_build* sim/results.xml soc.json pack.fs firmware/firmware.bin firmware/firmware.hex build_arch_test zephyr_workspace/app/build $(ZEPHYR_BUILD_DIR)
