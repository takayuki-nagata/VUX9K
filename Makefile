# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# ===== Toolchain Variables & Setup =====

VERYL = veryl
RUFF = ruff
YOSYS = yosys
GOWIN_PACK = gowin_pack
NEXTPNR ?= nextpnr-himbaechel
OPENFPGALOADER ?= openFPGALoader
CST_FILE ?= tangnano9k.cst
CARGO = cargo
PYTHON = python3
# cocotb testbenches run through pytest + cocotb_tools.runner (sim/runners/).
# Short tests are dominated by compile time -> Icarus; long SoC/GLS runs by simulation
# speed -> Verilator (~25-140x faster there). Override with SIM_UNIT= / SIM_SOC=.
SIM_UNIT ?= icarus
SIM_SOC ?= verilator
SIM_TESTS = sim/runners/test_sim.py
PYTEST_SIM = $(PYTHON) -m pytest -s -q
UV = uv

VENV_PATH ?= .venv
ZEPHYR_BASE ?= $(HOME)/zephyrproject/zephyr
ZEPHYR_SDK_INSTALL_DIR ?= $(HOME)/.local/zephyr-sdk-0.16.8
OSS_CAD_SUITE_BIN ?= $(HOME)/.local/oss-cad-suite/bin
CARGO_BIN ?= $(HOME)/.cargo/bin

export PATH := $(CURDIR)/$(VENV_PATH)/bin:$(OSS_CAD_SUITE_BIN):$(CARGO_BIN):$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH)

# Unified build output tree: every generated/build artifact lives under $(BUILD_DIR),
# keeping source directories (soc/cpu/, soc/, soc/uart/, firmware/, ...) generated-file-free.
BUILD_DIR := build
VERYL_OUT_DIR := $(BUILD_DIR)/veryl
FIRMWARE_BUILD_DIR := $(BUILD_DIR)/firmware
HACK_BUILD_DIR := $(BUILD_DIR)/hack
ZEPHYR_BUILD_DIR ?= $(BUILD_DIR)/zephyr
SYNTH_DIR := $(BUILD_DIR)/synth

.PHONY: all veryl check check-paths fmt test test-ci test-hw test-hardware build synth-top pnr bitstream build-hw prog-sram prog-flash clean venv setup firmware sim-unit sim-boot sim-soc sim test-isa zephyr-rust-lib zephyr-bc-lib build-zephyr sim-zephyr-emu sim-zephyr-repl sim-zephyr submodule-sync install-hack-tools build-hack sim-hack-emu sim-hack-pytest sim-hack-rtl sim-hack sim-hw-flow sim-gls-hw-flow sim-soc-fast sim-soc-fast-icarus sim-soc-gls-fast sim-gls-unit sim-gls sim-soc-mmio sim-sd-quirks sim-hw-flow-icarus test-slow test-sim sta coverage eqy timing FORCE

all: test-ci

venv: $(VENV_PATH)/bin/activate

$(VENV_PATH)/bin/activate:
	@if [ ! -d "$(VENV_PATH)" ]; then \
		echo "Creating virtual environment using uv..."; \
		$(UV) venv $(VENV_PATH) --python 3.13; \
	fi

setup: venv
	$(UV) pip install --python $(VENV_PATH)/bin/python cocotb pytest pyserial ruff
	@if [ -d ".git" ]; then \
		echo "Configuring Git core.hooksPath to .githooks..."; \
		git config core.hooksPath .githooks; \
	fi

check-paths:
	$(PYTHON) scripts/check_no_absolute_paths.py

check: check-paths
	$(VERYL) fmt --check
	$(VERYL) check
	$(RUFF) format --check
	$(RUFF) check

fmt:
	$(VERYL) fmt
	$(RUFF) format

# ===== Veryl Build =====

VERYL_SRCS = $(wildcard soc/*.veryl) $(wildcard soc/cpu/*.veryl) $(wildcard soc/uart/*.veryl) Veryl.toml

$(VERYL_OUT_DIR)/.stamp: $(VERYL_SRCS)
	@mkdir -p $(VERYL_OUT_DIR)
	$(VERYL) build --out-dir $(VERYL_OUT_DIR)
	@touch $(VERYL_OUT_DIR)/.stamp

veryl: $(VERYL_OUT_DIR)/.stamp

# ===== Firmware (Rust Boot Manager & Resident Loader) =====

FIRMWARE_SRCS = $(wildcard firmware/boot_manager/src/*.rs) $(wildcard firmware/boot_manager/bootstrap/*) firmware/boot_manager/Cargo.toml firmware/Cargo.toml
LOADER_SRCS = $(wildcard firmware/resident_loader/src/*.rs) firmware/resident_loader/link.x firmware/resident_loader/Cargo.toml

$(FIRMWARE_BUILD_DIR)/firmware.hex: $(FIRMWARE_SRCS) $(LOADER_SRCS)
	@mkdir -p $(FIRMWARE_BUILD_DIR)
	cd firmware/resident_loader && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/resident_loader $(FIRMWARE_BUILD_DIR)/resident_loader.bin
	cd firmware/boot_manager && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/boot_manager $(FIRMWARE_BUILD_DIR)/firmware.bin $(FIRMWARE_BUILD_DIR)
	$(PYTHON) scripts/merge_firmware_hex.py $(FIRMWARE_BUILD_DIR)/firmware.bin $(FIRMWARE_BUILD_DIR)/resident_loader.bin $(FIRMWARE_BUILD_DIR)/firmware.hex
	@# soc_ram.veryl's $$readmemh() resolves these bare filenames relative to each tool's own
	@# process cwd. yosys/nextpnr run from the repo root, so they need these symlinks (not cp, to stay
	@# identical to build/firmware/); cocotb tests get their own per-run symlinks from sim/runners/sim_runner.py.
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware.hex ./firmware.hex
	ln -sf $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d0.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d1.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d2.hex $(CURDIR)/$(FIRMWARE_BUILD_DIR)/firmware_d3.hex .

firmware: $(FIRMWARE_BUILD_DIR)/firmware.hex

# ===== Zephyr (bc_clone_rs Out-of-Tree App) =====

BC_APP_DIR ?= vendor/bc_clone_rs/examples/zephyr_app

submodule-sync:
	git submodule update --init --recursive

zephyr-rust-lib:
	cd zephyr_workspace/app/rust_demo && $(CARGO) build --release --target riscv32i-unknown-none-elf
	$(PYTHON) scripts/elf2bin.py zephyr_workspace/app/rust_demo/target/riscv32i-unknown-none-elf/release/standalone zephyr_workspace/app/rust_demo/app.bin

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
	$(MAKE) -C hack_demo

# ===== Simulation - Unit Tests =====

sim-unit: veryl
	@echo "=== Running RTL Unit Tests (cocotb) ==="
	SIM=$(SIM_UNIT) $(PYTEST_SIM) "$(SIM_TESTS)::test_unit"

# ===== Simulation - RV32I ISA Tests (riscv-tests) =====

test-isa: veryl
	@echo "=== Running riscv-tests (rv32ui/rv32mi) on tb_hex_runner ==="
	$(PYTHON) scripts/run_riscv_tests.py

# ===== Simulation - SoC Integration =====

sim-boot: veryl firmware
	@echo "=== Running Hardware Boot Manager & Bridge Cocotb Tests ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_boot]"

sim-soc: veryl firmware sim-boot
	@echo "=== Running Python Software Emulator Unit Tests ==="
	$(PYTHON) -m pytest sim/emulator/test_emulator.py
	@echo "=== Running Python Software Emulator ==="
	$(PYTHON) sim/emulator/emulator.py $(FIRMWARE_BUILD_DIR)/firmware.bin
	@echo "=== Running SoC Top RISC-V Integration Tests ==="
	SIM=$(SIM_UNIT) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_rv32i]"
	@echo "=== Running SoC Top Hack Integration Tests ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_hack]"

sim-zephyr-emu: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Python Emulator ==="
	@if [ -f "$(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin" ]; then \
		$(PYTHON) sim/emulator/emulator.py $(ZEPHYR_BUILD_DIR)/zephyr/zephyr.bin --steps 120000000 --until "bc> "; \
	else \
		$(PYTHON) sim/emulator/emulator.py $(FIRMWARE_BUILD_DIR)/firmware.bin; \
	fi

sim-zephyr-repl: build-zephyr
	@echo "=== Running Zephyr bc_clone_rs Self-Tests & REPL Pytest Suite ==="
	$(PYTHON) -m pytest sim/emulator/test_soc_bc.py

sim-zephyr: sim-zephyr-emu sim-zephyr-repl

sim-hack-emu: build-hack
	@echo "=== Running Hack Firmware on Python SoC Emulator ==="
	$(PYTHON) sim/emulator/emulator.py $(HACK_BUILD_DIR)/firmware.bin

sim-hack-pytest: build-hack
	@echo "=== Running Hack Firmware Pytest Test Suite ==="
	$(PYTHON) -m pytest sim/emulator/test_hack_firmware.py

sim-hack-rtl: veryl build-hack
	@echo "=== Running Hack 16-bit SoC RTL Simulation ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_hack]"

sim-hack: sim-hack-emu sim-hack-pytest sim-hack-rtl

sim-hw-flow: veryl firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (RTL Simulation) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_hardware_flow]"

sim-gls-hw-flow: synth-top firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (GLS Simulation) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc_gls[test_soc_hardware_flow]"

sim: sim-unit test-isa sim-gls-unit sim-soc-fast sim-soc-mmio sim-boot sim-hack-rtl sim-sd-quirks sim-hw-flow

# ===== Synthesis / PnR / STA / Bitstream / Programming =====

# synth_gowin options, shared by the SoC and the unit gate-level netlists (AGENTS.md: "Synthesis flags").
# -nowidelut: no MUX2_LUT5..8 wide-LUT muxes. Behavior-neutral; they made the mapping (and timing)
#   swing by hundreds of LUTs on small RTL changes and routed worse.
# -no-rw-check: no collision emulation for BSRAM read/write to the same address in one cycle. It put
#   the combinational fetch address (pc_out) on the critical path; the only collision is a store to
#   the next instruction's I-RAM word, whose fetch is then undefined (not supported; see README).
SYNTH_GOWIN_OPTS = -nowidelut -no-rw-check
SYNTH_OPTS_STAMP := $(SYNTH_DIR)/.synth_gowin_opts

# Rewritten only when SYNTH_GOWIN_OPTS changes, so new options re-synthesize soc.json
$(SYNTH_OPTS_STAMP): FORCE
	@mkdir -p $(SYNTH_DIR)
	@echo "$(SYNTH_GOWIN_OPTS)" | cmp -s - $@ || echo "$(SYNTH_GOWIN_OPTS)" > $@

synth-units: veryl
	@echo "=== Synthesizing Submodules to Gowin Netlists for GLS Unit Tests ==="
	@mkdir -p $(SYNTH_DIR)
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/soc/cpu/auto_mode_detector.sv $(VERYL_OUT_DIR)/soc/cpu/hack_translator.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_alu.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_decode.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_regfile.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_csrs.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_lsu.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_trap_unit.sv $(VERYL_OUT_DIR)/soc/cpu/next_pc_unit.sv $(VERYL_OUT_DIR)/soc/cpu/unified_cpu.sv; synth_gowin -top unified_cpu $(SYNTH_GOWIN_OPTS); write_verilog -noattr $(SYNTH_DIR)/unified_cpu_syn.v"
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/soc/uart/clk_timer.sv $(VERYL_OUT_DIR)/soc/uart/shift_registers.sv $(VERYL_OUT_DIR)/soc/uart/fifo_sync.sv $(VERYL_OUT_DIR)/soc/uart/uart_tx.sv $(VERYL_OUT_DIR)/soc/uart/uart_rx.sv $(VERYL_OUT_DIR)/soc/uart/uart_controller.sv; synth_gowin -top uart_controller $(SYNTH_GOWIN_OPTS); write_verilog -noattr $(SYNTH_DIR)/uart_controller_syn.v"
	$(YOSYS) -p "read_verilog -sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/soc/cpu/auto_mode_detector.sv; synth_gowin -top auto_mode_detector $(SYNTH_GOWIN_OPTS); write_verilog -noattr $(SYNTH_DIR)/auto_mode_detector_syn.v"

sim-gls-unit: synth-units
	@echo "=== Running Gowin Primitive GLS Unit Tests (cocotb) ==="
	SIM=$(SIM_UNIT) $(PYTEST_SIM) "$(SIM_TESTS)::test_unit_gls"

sim-soc-fast: veryl firmware
	@echo "=== Running Fast SoC Top Boot & Execution Verification (RTL) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_fast]" "$(SIM_TESTS)::test_soc[test_soc_boot_mode]"

# Same test on Icarus: 4-state simulation keeps X-propagation (e.g. a missing reset)
# visible on the boot path, which 2-state Verilator would hide
sim-soc-fast-icarus: veryl firmware
	@echo "=== Running Fast SoC Top Boot & Execution Verification (RTL, Icarus 4-state) ==="
	SIM=icarus $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_fast]" "$(SIM_TESTS)::test_soc[test_soc_boot_mode]"

sim-soc-mmio: veryl
	@echo "=== Running SoC Memory Map / MMIO Peripheral Tests (RTL) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_rv32i]" "$(SIM_TESTS)::test_soc[test_soc_hack_mmio]"

sim-sd-quirks: veryl firmware
	@echo "=== Running SD Card Edge-Case Tests (strict / SDSC SD model, RTL) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_sd_quirks]"

# Full flashing flow on Icarus: 4-state coverage of the longest RTL run (~15 min; test-slow only)
# Formal equivalence of the working tree's RTL against EQY_BASE (scripts/run_eqy.py), for
# behavior-preserving refactors: `make eqy EQY_BASE=<commit> EQY_TOP=unified_cpu|soc_top`.
# soc_ram is a black box on both sides. Not part of test-sim (it depends on the base commit).
EQY_BASE ?= HEAD
EQY_TOP ?= unified_cpu
EQY_NOMATCH ?=

eqy: veryl
	$(PYTHON) scripts/run_eqy.py --base $(EQY_BASE) --top $(EQY_TOP) --veryl $(VERYL) $(if $(EQY_NOMATCH),--nomatch $(EQY_NOMATCH))

# Line + toggle coverage of the Verilator RTL runs (unit + SoC, incl. the slow hw-flow),
# merged and annotated onto the generated .sv under build/coverage/. Icarus-only runs
# (test-isa) and GLS aren't measured. The merge also runs when a test fails; the
# target still fails then.
COVERAGE_DIR := $(BUILD_DIR)/coverage

coverage: veryl firmware build-hack
	@echo "=== Measuring Verilator line/toggle coverage (RTL unit + SoC tests) ==="
	@rm -rf $(COVERAGE_DIR) && mkdir -p $(COVERAGE_DIR)
	HDL_COVERAGE=1 SIM=verilator $(PYTEST_SIM) "$(SIM_TESTS)::test_unit" "$(SIM_TESTS)::test_soc"; rc=$$?; \
	verilator_coverage --write $(COVERAGE_DIR)/merged.dat --write-info $(COVERAGE_DIR)/merged.info \
		$(BUILD_DIR)/sim/verilator-cov/*/run_*/coverage.dat && \
	verilator_coverage --annotate $(COVERAGE_DIR)/annotated --annotate-min 1 $(COVERAGE_DIR)/merged.dat && \
	echo "=== Annotated sources: $(COVERAGE_DIR)/annotated ===" && exit $$rc

sim-hw-flow-icarus: veryl firmware build-hack
	@echo "=== Running SoC Top End-to-End Hardware Verification Flow (RTL, Icarus 4-state) ==="
	SIM=icarus $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_hardware_flow]"

sim-soc-gls-fast: synth-top firmware
	@echo "=== Running Fast SoC Top Boot & Execution Verification (GLS Netlist) ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc_gls[test_soc_gls_fast]"

SOC_RTL_SRCS = $(VERYL_OUT_DIR)/soc/soc_pkg.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_pkg.sv $(VERYL_OUT_DIR)/soc/cpu/auto_mode_detector.sv $(VERYL_OUT_DIR)/soc/cpu/hack_translator.sv \
               $(VERYL_OUT_DIR)/soc/cpu/rv32i_alu.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_decode.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_regfile.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_csrs.sv \
               $(VERYL_OUT_DIR)/soc/cpu/rv32i_lsu.sv $(VERYL_OUT_DIR)/soc/cpu/rv32i_trap_unit.sv $(VERYL_OUT_DIR)/soc/cpu/next_pc_unit.sv $(VERYL_OUT_DIR)/soc/cpu/unified_cpu.sv $(VERYL_OUT_DIR)/soc/uart/clk_timer.sv $(VERYL_OUT_DIR)/soc/uart/fifo_sync.sv $(VERYL_OUT_DIR)/soc/uart/shift_registers.sv \
               $(VERYL_OUT_DIR)/soc/uart/uart_tx.sv $(VERYL_OUT_DIR)/soc/uart/uart_rx.sv $(VERYL_OUT_DIR)/soc/uart/uart_controller.sv $(VERYL_OUT_DIR)/soc/timer_core.sv \
               $(VERYL_OUT_DIR)/soc/sdcard_spi.sv $(VERYL_OUT_DIR)/soc/gpio_controller.sv $(VERYL_OUT_DIR)/soc/soc_ram.sv $(VERYL_OUT_DIR)/soc/soc_addr_decoder.sv $(VERYL_OUT_DIR)/soc/soc_top.sv

$(SYNTH_DIR)/soc.json $(SYNTH_DIR)/soc_syn.v: $(VERYL_OUT_DIR)/.stamp $(FIRMWARE_BUILD_DIR)/firmware.hex $(SOC_RTL_SRCS) $(SYNTH_OPTS_STAMP)
	@mkdir -p $(SYNTH_DIR)
	$(YOSYS) -p "\
		read_verilog -sv $(SOC_RTL_SRCS); \
		synth_gowin -top soc_top $(SYNTH_GOWIN_OPTS) -json $(SYNTH_DIR)/soc.json; \
		write_verilog -noattr $(SYNTH_DIR)/soc_syn.v; \
	"

synth-top: $(SYNTH_DIR)/soc.json

sim-gls: sim-gls-unit sim-soc-gls-fast

# nextpnr seeds, tried in parallel (scripts/run_pnr.py): the first seed to meet timing is adopted
# (else the best finished one) and recorded in pnr_seed.json. A seed finishing below
# PNR_ABORT_SLACK stops the rest (PNR_ABORT_SLACK=none: keep going); `make timing` routes every seed.
PNR_SEEDS ?= 2 3 5 7 11
PNR_ABORT_SLACK ?= -1.5
PNR_SEEDS_STAMP := $(SYNTH_DIR)/.pnr_seeds

FORCE:

# Rewritten only when PNR_SEEDS changes, so a different seed list re-runs PnR
$(PNR_SEEDS_STAMP): FORCE
	@mkdir -p $(SYNTH_DIR)
	@echo "$(PNR_SEEDS)" | cmp -s - $@ || echo "$(PNR_SEEDS)" > $@

PNR_ARGS = --device GW1NR-LV9QN88PC6/I5 --vopt family=GW1N-9C --vopt cst=$(CST_FILE) --json $(SYNTH_DIR)/soc.json \
	--write $(SYNTH_DIR)/soc_pnr.json --report $(SYNTH_DIR)/soc_sta.json --freq 30.0 --seeds $(PNR_SEEDS) \
	--seed-dir $(SYNTH_DIR)/pnr --seed-info $(SYNTH_DIR)/pnr_seed.json

$(SYNTH_DIR)/soc_pnr.json $(SYNTH_DIR)/soc_sta.json $(SYNTH_DIR)/pnr_seed.json &: $(SYNTH_DIR)/soc.json $(CST_FILE) $(PNR_SEEDS_STAMP)
	$(PYTHON) scripts/run_pnr.py $(PNR_ARGS) --abort-slack $(PNR_ABORT_SLACK)

pnr: $(SYNTH_DIR)/soc_pnr.json
	@echo "=== Routed with nextpnr seed $$($(PYTHON) -c 'import json,sys; print(json.load(open(sys.argv[1]))["seed"])' $(SYNTH_DIR)/pnr_seed.json) ==="

sta: $(SYNTH_DIR)/soc_sta.json
	@echo "=== Generating Static Timing Analysis (STA) Report (Target: 30.0 MHz, 10% Safety Margin) ==="
	$(PYTHON) scripts/report_sta.py $(SYNTH_DIR)/soc_sta.json --freq 30.0 --seed-info $(SYNTH_DIR)/pnr_seed.json --netlist $(SYNTH_DIR)/soc_pnr.json --strict

# Area + timing record for one RTL commit of the timing work: always routes every seed
# (--all-seeds: no early stop, not even on closure), then prints cell counts, per-seed slack and the worst path's end points and
# appends a row to build/timing/history.tsv. Leaves soc_pnr/soc_sta/pnr_seed.json consistent.
timing: $(SYNTH_DIR)/soc.json $(CST_FILE) $(PNR_SEEDS_STAMP)
	$(PYTHON) scripts/run_pnr.py $(PNR_ARGS) --all-seeds
	$(PYTHON) scripts/timing_summary.py --synth-dir $(SYNTH_DIR) --seeds $(PNR_SEEDS) --freq 30.0 \
		--history $(BUILD_DIR)/timing/history.tsv

$(SYNTH_DIR)/pack.fs: $(SYNTH_DIR)/soc_pnr.json
	$(GOWIN_PACK) -d GW1N-9C -o $(SYNTH_DIR)/pack.fs $(SYNTH_DIR)/soc_pnr.json

bitstream: $(SYNTH_DIR)/pack.fs
	@echo "=== pack.fs routed with nextpnr seed $$($(PYTHON) -c 'import json,sys; print(json.load(open(sys.argv[1]))["seed"])' $(SYNTH_DIR)/pnr_seed.json) ==="

build-hw: $(SYNTH_DIR)/pack.fs
	@echo "=== Hardware Bitstream pack.fs Built Successfully! ==="

prog-sram: $(SYNTH_DIR)/pack.fs
	$(OPENFPGALOADER) -b tangnano9k $(SYNTH_DIR)/pack.fs

prog-flash: $(SYNTH_DIR)/pack.fs
	$(OPENFPGALOADER) -b tangnano9k -f $(SYNTH_DIR)/pack.fs

# ===== Aggregate Test Targets =====

# Every push/PR (CI). Long SoC runs are on Verilator (SIM_SOC); see AGENTS.md for timings.
test-sim: check firmware zephyr-rust-lib zephyr-bc-lib build-hack build-zephyr sim-unit test-isa sim-gls-unit sim-soc-fast sim-soc-fast-icarus sim-soc-mmio sim-boot sim-hack-rtl sim-sd-quirks sim-hw-flow synth-top sim-soc-gls-fast
	@echo "========================================================================"
	@echo "  [SIM] ALL RTL, GLS NETLIST, ISA & SOC SIMULATION TESTS PASSED!        "
	@echo "========================================================================"

# Nightly / on demand (CI schedule + workflow_dispatch): the gate-level flashing flow and
# the full flow on 4-state Icarus
test-slow: sim-gls-hw-flow sim-hw-flow-icarus
	@echo "========================================================================"
	@echo "  [SLOW] GLS FLASHING FLOW & ICARUS FULL-FLOW TESTS PASSED!             "
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
	cd zephyr_workspace/app/rust_demo && $(CARGO) clean
	cd vendor/bc_clone_rs/crates/bc_zephyr 2>/dev/null && $(CARGO) clean || true
	$(MAKE) -C hack_demo clean
	rm -rf $(BUILD_DIR) ./firmware.hex ./firmware_d*.hex zephyr_workspace/app/build
