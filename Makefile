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

.PHONY: all emu emu-py emu-test test-isa-emu test-emu test-fw-host firmware-size coverage-fw sim-lockstep sim-lockstep-slow veryl check check-paths fmt test test-ci test-hw test-hw-dist dist check-dist test-hardware build synth-top pnr bitstream build-hw prog-sram prog-flash clean venv setup firmware hwtest hw-smoke sim-unit sim-boot sim-soc sim test-isa zephyr-bc-lib build-zephyr build-zephyr-demo sim-zephyr-repl sim-zephyr-demo-rtl sim-zephyr-demo-gls sim-zephyr submodule-sync install-hack-tools build-hack sim-hack-emu sim-hack-pytest sim-hack-rtl sim-hack sim-hw-flow sim-gls-hw-flow sim-soc-fast sim-soc-fast-icarus sim-soc-gls-fast sim-gls-unit sim-gls sim-soc-mmio sim-sd-quirks sim-hw-flow-icarus test-slow test-sim sta coverage eqy timing FORCE

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

# Board self-test (firmware/hw_test), an I-RAM image in place of the Boot Manager for
# `make hw-smoke`; its D-RAM preload (elf2bin's firmware_d*.hex) goes to its own
# directory and must stay all zero (scripts/hw_smoke.py checks it)
HWTEST_DIR = $(FIRMWARE_BUILD_DIR)/hw_test
HWTEST_SRCS = $(wildcard firmware/hw_test/src/*) firmware/hw_test/Cargo.toml $(wildcard firmware/fw_common/src/*.rs) \
	firmware/boot_manager/bootstrap/link.x

$(HWTEST_DIR)/hw_test.hex: $(HWTEST_SRCS)
	@mkdir -p $(HWTEST_DIR)
	cd firmware/hw_test && $(CARGO) build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/hw_test $(HWTEST_DIR)/hw_test.bin $(HWTEST_DIR)
	$(PYTHON) scripts/merge_firmware_hex.py $(HWTEST_DIR)/hw_test.bin "" $(HWTEST_DIR)/hw_test.hex

hwtest: $(HWTEST_DIR)/hw_test.hex

# Real board: run hw_test on every routed seed's placement (build/synth/pnr/seed_*; `make
# timing` routes them all), with only the block-RAM contents changed. See scripts/hw_smoke.py.
hw-smoke: hwtest firmware
	$(PYTHON) scripts/hw_smoke.py --hex $(HWTEST_DIR)/hw_test.hex --base-hex $(FIRMWARE_BUILD_DIR)/firmware.hex \
		--seed-dir $(SYNTH_DIR)/pnr --out $(BUILD_DIR)/hw_smoke

# Line coverage of the firmware and demos from the emulator tests (sim/emu): see
# scripts/coverage_fw.py. Minimums in coverage/thresholds.toml; report in build/coverage/fw/
FW_COV_DIR = $(BUILD_DIR)/coverage/fw
FW_TARGET = firmware/target/riscv32i-unknown-none-elf
coverage-fw: emu-py firmware build-hack build-zephyr-demo build-zephyr
	rm -rf $(FW_COV_DIR) && mkdir -p $(FW_COV_DIR)
	cd firmware/boot_manager && $(CARGO) build --profile coverage
	cd firmware/resident_loader && $(CARGO) build --profile coverage
	$(PYTHON) scripts/elf2bin.py $(FW_TARGET)/coverage/boot_manager $(FW_COV_DIR)/boot_manager.bin > /dev/null
	$(PYTHON) scripts/elf2bin.py $(FW_TARGET)/coverage/resident_loader $(FW_COV_DIR)/resident_loader.bin > /dev/null
	cmp $(FW_COV_DIR)/boot_manager.bin $(FIRMWARE_BUILD_DIR)/firmware.bin
	cmp -i 0x3800 $(FW_COV_DIR)/resident_loader.bin $(FIRMWARE_BUILD_DIR)/resident_loader.bin
	@# The Hack demo's assembly, made the way hack_demo/Makefile made the binary
	if command -v msp430-gcc >/dev/null 2>&1 && [ -f $(VENV_PATH)/bin/hcc ]; then \
		$(VENV_PATH)/bin/hcc -S hack_demo/src/main.c -o $(FW_COV_DIR)/hack_demo.asm; \
	else cp hack_demo/src/main.asm $(FW_COV_DIR)/hack_demo.asm; fi
	VUX9K_COV_DIR=$(FW_COV_DIR)/hits $(PYTHON) -m pytest -q sim/emu
	$(PYTHON) scripts/coverage_fw.py --hits $(FW_COV_DIR)/hits \
		--elf boot_manager=$(FW_TARGET)/coverage/boot_manager=firmware/boot_manager/,firmware/fw_common/ \
		--elf resident_loader=$(FW_TARGET)/coverage/resident_loader=firmware/resident_loader/ \
		--elf zephyr_demo=$(ZEPHYR_DEMO_BUILD_DIR)/zephyr/zephyr.elf=zephyr_workspace/app/ \
		--hack hack_demo=$(FW_COV_DIR)/hack_demo.asm=$(HACK_BUILD_DIR)/firmware.bin \
		--lcov $(FW_COV_DIR)/fw.info --summary $(FW_COV_DIR)/summary.md --thresholds coverage/thresholds.toml

# Host tests of the firmware's hardware-independent logic (firmware/fw_common)
HOST_TARGET ?= $(shell rustc -vV | sed -n 's/^host: //p')
test-fw-host:
	cd firmware && $(CARGO) test -p fw_common --target $(HOST_TARGET)

# Size budgets: the Boot Manager fills lower I-RAM below the Resident Loader
# (0x0000-0x37FF), the Resident Loader its 2 KB above it (0x3800-0x3FFF)
BM_MAX_BYTES = 14336
RL_MAX_BYTES = 2048
firmware-size: firmware
	@bm=$$(stat -c %s $(FIRMWARE_BUILD_DIR)/firmware.bin); \
	rl=$$(( $$(stat -c %s $(FIRMWARE_BUILD_DIR)/resident_loader.bin) - 0x3800 )); \
	echo "Boot Manager    $$bm / $(BM_MAX_BYTES) bytes"; \
	echo "Resident Loader $$rl / $(RL_MAX_BYTES) bytes"; \
	[ $$bm -le $(BM_MAX_BYTES) ] && [ $$rl -le $(RL_MAX_BYTES) ] || { echo "firmware over its size budget"; exit 1; }

# ===== Emulator (emu/: host-only Rust workspace, toolchain pinned by emu/rust-toolchain.toml) =====

EMU_TARGET_DIR = $(BUILD_DIR)/emu/target
EMU_PY_DIR = $(BUILD_DIR)/emu/python
# PYO3_PYTHON: the pyo3 build script inspects this interpreter (abi3, so any >= 3.10 works)
EMU_CARGO = cd emu && CARGO_TARGET_DIR=$(CURDIR)/$(EMU_TARGET_DIR) PYO3_PYTHON=$$(command -v $(PYTHON)) $(CARGO)

emu:
	$(EMU_CARGO) build --release

# Python module: plain cargo build of the cdylib, copied under the name Python imports
emu-py: emu
	mkdir -p $(EMU_PY_DIR)
	cp $(EMU_TARGET_DIR)/release/libvux9k_emu.so $(EMU_PY_DIR)/vux9k_emu.abi3.so

emu-test:
	$(EMU_CARGO) test
	$(EMU_CARGO) clippy --all-targets -- -D warnings
	$(EMU_CARGO) fmt --check

# ===== Zephyr (bc_clone_rs Out-of-Tree App) =====

BC_APP_DIR ?= vendor/bc_clone_rs/examples/zephyr_app

submodule-sync:
	git submodule update --init --recursive

zephyr-bc-lib:
	cd vendor/bc_clone_rs/crates/bc_zephyr && $(CARGO) build --release --target riscv32i-unknown-none-elf --no-default-features

# west build <board> <app dir> <build dir> [extra cmake args]; skipped without Zephyr
define west_build
	@if [ -d "$(ZEPHYR_BASE)" ] && [ -d "$(ZEPHYR_SDK_INSTALL_DIR)" ]; then \
		export PATH=$(CURDIR)/$(VENV_PATH)/bin:$(ZEPHYR_SDK_INSTALL_DIR)/riscv64-zephyr-elf/bin:$(PATH) && \
		export ZEPHYR_BASE=$(ZEPHYR_BASE) && \
		export ZEPHYR_SDK_INSTALL_DIR=$(ZEPHYR_SDK_INSTALL_DIR) && \
		export ZEPHYR_TOOLCHAIN_VARIANT=zephyr && \
		west build -p auto -b $(1) $(2) -d $(3) -- \
			-DBOARD_ROOT=$(CURDIR)/zephyr_workspace \
			-DSOC_ROOT=$(CURDIR)/zephyr_workspace \
			-DEXTRA_ZEPHYR_MODULES=$(CURDIR)/zephyr_workspace $(4); \
	else \
		echo "Zephyr or Zephyr SDK not found. Skipping the Zephyr build of $(2)."; \
	fi
endef

# bc needs ~260 KB: it runs only on the emulator's extended profile (NOT real hardware)
build-zephyr:
	@echo "=== Building Zephyr bc_clone_rs Application (vux9k/vux9k/ext, emulator only) ==="
	$(call west_build,vux9k/vux9k/ext,$(BC_APP_DIR),$(ZEPHYR_BUILD_DIR),-DRUST_TARGET=riscv32i-unknown-none-elf)

# The Rust demo on Zephyr for the real board; the link fails if it outgrows 14 KB / 8 KB
ZEPHYR_DEMO_BUILD_DIR ?= $(BUILD_DIR)/zephyr-demo
build-zephyr-demo:
	@echo "=== Building the Zephyr Rust demo (vux9k) ==="
	$(call west_build,vux9k,zephyr_workspace/app,$(ZEPHYR_DEMO_BUILD_DIR),)

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

# The same riscv-tests on the Rust emulator (isa-test profile): must match test-isa
test-isa-emu: emu-py
	$(PYTHON) scripts/run_riscv_tests.py --backend emu

LOCKSTEP_TRACE_FILE = $(BUILD_DIR)/sim/verilator/tb_soc_top/run_test_soc_lockstep/lockstep.trace

# RTL <-> emulator lockstep: trace the programs on the RTL (Verilator), compare on the emulator
sim-lockstep: veryl firmware build-hack emu-py
	SIM=verilator $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_lockstep]"
	LOCKSTEP_TRACE=$(LOCKSTEP_TRACE_FILE) $(PYTHON) -m pytest sim/emu/test_lockstep.py

# The long programs, plus random ones for LOCKSTEP_SEEDS (default 101,102,103)
LOCKSTEP_SEEDS ?= 101,102,103
sim-lockstep-slow: veryl firmware build-hack emu-py
	LOCKSTEP_SEEDS=$(LOCKSTEP_SEEDS) LOCKSTEP_SLOW=1 SIM=verilator $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_lockstep]"
	LOCKSTEP_SEEDS=$(LOCKSTEP_SEEDS) LOCKSTEP_SLOW=1 LOCKSTEP_TRACE=$(LOCKSTEP_TRACE_FILE) $(PYTHON) -m pytest sim/emu/test_lockstep.py

test-emu: emu-py firmware hwtest build-hack build-zephyr build-zephyr-demo
	@echo "=== Running firmware and demo tests on the emulator ==="
	$(PYTHON) -m pytest sim/emu

test-isa: veryl
	@echo "=== Running riscv-tests (rv32ui/rv32mi) on tb_hex_runner ==="
	$(PYTHON) scripts/run_riscv_tests.py

# ===== Simulation - SoC Integration =====

sim-boot: veryl firmware
	@echo "=== Running Hardware Boot Manager & Bridge Cocotb Tests ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_boot]"

sim-soc: veryl firmware sim-boot test-emu
	@echo "=== Running SoC Top RISC-V Integration Tests ==="
	SIM=$(SIM_UNIT) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_rv32i]"
	@echo "=== Running SoC Top Hack Integration Tests ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_hack]"

sim-zephyr-repl: build-zephyr emu-py
	@echo "=== Running Zephyr bc_clone_rs self-tests & REPL on the emulator (extended profile) ==="
	$(PYTHON) -m pytest sim/emu/test_zephyr_bc.py

sim-zephyr-demo-rtl: veryl firmware build-zephyr-demo
	@echo "=== Running the Zephyr Rust demo from SD on the RTL ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc[test_soc_zephyr_demo]"

sim-zephyr-demo-gls: synth-top firmware build-zephyr-demo
	@echo "=== Running the Zephyr Rust demo from SD on the GLS netlist ==="
	SIM=$(SIM_SOC) $(PYTEST_SIM) "$(SIM_TESTS)::test_soc_gls[test_soc_zephyr_demo]"

sim-zephyr: sim-zephyr-repl sim-zephyr-demo-rtl

sim-hack-emu: emu build-hack
	@echo "=== Running the Hack demo on the emulator (vux9k-emu) ==="
	$(EMU_TARGET_DIR)/release/vux9k-emu --no-firmware --no-card --load $(HACK_BUILD_DIR)/firmware.bin --mode hack --until "(100%)!"

sim-hack-pytest: emu-py build-hack
	@echo "=== Running the Hack demo tests on the emulator ==="
	$(PYTHON) -m pytest sim/emu/test_hack_demo.py

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
               $(VERYL_OUT_DIR)/soc/sdcard_spi.sv $(VERYL_OUT_DIR)/soc/gpio_controller.sv $(VERYL_OUT_DIR)/soc/soc_ram.sv $(VERYL_OUT_DIR)/soc/soc_addr_decoder.sv $(VERYL_OUT_DIR)/soc/soc_top.sv $(VERYL_OUT_DIR)/soc/board_top.sv

$(SYNTH_DIR)/soc.json $(SYNTH_DIR)/soc_syn.v: $(VERYL_OUT_DIR)/.stamp $(FIRMWARE_BUILD_DIR)/firmware.hex $(SOC_RTL_SRCS) $(SYNTH_OPTS_STAMP)
	@mkdir -p $(SYNTH_DIR)
	$(YOSYS) -p "\
		read_verilog -sv $(SOC_RTL_SRCS); \
		synth_gowin -top board_top $(SYNTH_GOWIN_OPTS) -json $(SYNTH_DIR)/soc.json; \
		write_verilog -noattr $(SYNTH_DIR)/soc_syn.v; \
	"

synth-top: $(SYNTH_DIR)/soc.json

sim-gls: sim-gls-unit sim-soc-gls-fast

# nextpnr seeds, tried in parallel (scripts/run_pnr.py): the first seed to meet timing is adopted
# (else the best finished one) and recorded in pnr_seed.json. A seed finishing below
# PNR_ABORT_SLACK stops the rest (PNR_ABORT_SLACK=none: keep going); `make timing` routes every seed.
PNR_SEEDS ?= 2 3 5 7 11
# STA target for the SoC clock (board_top's PLL output, soc_pkg::CLK_HZ = 18 MHz): 1.5x.
# nextpnr's delays proved optimistic on the board: placements with a 33-39 MHz STA Fmax
# failed at 27 MHz and passed at 24 MHz (README, "Clock"). nextpnr applies --freq to the
# PLL output net as well; the 27 MHz crystal net itself only feeds the PLL.
STA_FREQ ?= 27.0
PNR_ABORT_SLACK ?= -1.5
PNR_SEEDS_STAMP := $(SYNTH_DIR)/.pnr_seeds

FORCE:

# Rewritten only when PNR_SEEDS or STA_FREQ changes, so a different seed list or target re-runs PnR
$(PNR_SEEDS_STAMP): FORCE
	@mkdir -p $(SYNTH_DIR)
	@echo "$(PNR_SEEDS) @ $(STA_FREQ)" | cmp -s - $@ || echo "$(PNR_SEEDS) @ $(STA_FREQ)" > $@

PNR_ARGS = --device GW1NR-LV9QN88PC6/I5 --vopt family=GW1N-9C --vopt cst=$(CST_FILE) --json $(SYNTH_DIR)/soc.json \
	--write $(SYNTH_DIR)/soc_pnr.json --report $(SYNTH_DIR)/soc_sta.json --freq $(STA_FREQ) --seeds $(PNR_SEEDS) \
	--seed-dir $(SYNTH_DIR)/pnr --seed-info $(SYNTH_DIR)/pnr_seed.json

$(SYNTH_DIR)/soc_pnr.json $(SYNTH_DIR)/soc_sta.json $(SYNTH_DIR)/pnr_seed.json &: $(SYNTH_DIR)/soc.json $(CST_FILE) $(PNR_SEEDS_STAMP)
	$(PYTHON) scripts/run_pnr.py $(PNR_ARGS) --abort-slack $(PNR_ABORT_SLACK)

pnr: $(SYNTH_DIR)/soc_pnr.json
	@echo "=== Routed with nextpnr seed $$($(PYTHON) -c 'import json,sys; print(json.load(open(sys.argv[1]))["seed"])' $(SYNTH_DIR)/pnr_seed.json) ==="

sta: $(SYNTH_DIR)/soc_sta.json
	@echo "=== Generating Static Timing Analysis (STA) Report (Target: $(STA_FREQ) MHz, 1.5x the 18 MHz SoC clock) ==="
	$(PYTHON) scripts/report_sta.py $(SYNTH_DIR)/soc_sta.json --freq $(STA_FREQ) --seed-info $(SYNTH_DIR)/pnr_seed.json --netlist $(SYNTH_DIR)/soc_pnr.json --strict

# Area + timing record for one RTL commit of the timing work: always routes every seed
# (--all-seeds: no early stop, not even on closure), then prints cell counts, per-seed slack and the worst path's end points and
# appends a row to build/timing/history.tsv. Leaves soc_pnr/soc_sta/pnr_seed.json consistent.
timing: $(SYNTH_DIR)/soc.json $(CST_FILE) $(PNR_SEEDS_STAMP)
	$(PYTHON) scripts/run_pnr.py $(PNR_ARGS) --all-seeds
	$(PYTHON) scripts/timing_summary.py --synth-dir $(SYNTH_DIR) --seeds $(PNR_SEEDS) --freq $(STA_FREQ) \
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
test-sim: check emu-test test-isa-emu firmware test-fw-host firmware-size build-zephyr-demo zephyr-bc-lib build-hack test-emu coverage-fw sim-lockstep build-zephyr sim-unit test-isa sim-gls-unit sim-soc-fast sim-soc-fast-icarus sim-soc-mmio sim-boot sim-hack-rtl sim-sd-quirks sim-hw-flow sim-zephyr-demo-rtl synth-top sim-soc-gls-fast
	@echo "========================================================================"
	@echo "  [SIM] ALL RTL, GLS NETLIST, ISA & SOC SIMULATION TESTS PASSED!        "
	@echo "========================================================================"

# Nightly / on demand (CI schedule + workflow_dispatch): the gate-level flashing flow and
# the full flow on 4-state Icarus
test-slow: sim-gls-hw-flow sim-hw-flow-icarus sim-lockstep-slow sim-zephyr-demo-gls
	@echo "========================================================================"
	@echo "  [SLOW] GLS FLASHING FLOW, ICARUS FULL-FLOW & LONG LOCKSTEP PASSED!    "
	@echo "========================================================================"

test: test-sim sta
	@echo "========================================================================"
	@echo "  [TEST] ALL SIMULATION & STATIC TIMING ANALYSIS (STA) PASSED 100%!     "
	@echo "========================================================================"

test-ci: test

test-hardware: test-hw

test-hw: build-zephyr-demo firmware build-hack build-hw prog-sram
	@echo "=== Running Automated End-to-End Hardware Test Suite on Tang Nano 9K ==="
	$(PYTHON) scripts/test_hardware.py
	@echo "========================================================================"
	@echo "  [HW] ALL REAL TANG NANO 9K HARDWARE & SD CARD TESTS PASSED 100%!     "
	@echo "========================================================================"

# The application-developer distribution (docs/APP_DEVELOPMENT.md) in build/dist/;
# check-dist runs its demos on its own emulator, away from the repository
dist: bitstream firmware emu-py build-hack build-zephyr-demo
	$(PYTHON) scripts/make_dist.py --out $(BUILD_DIR)/dist

check-dist:
	$(PYTHON) scripts/check_dist.py $(BUILD_DIR)/dist
	$(PYTHON) scripts/release_check.py selftest $(BUILD_DIR)/dist

# test-hw on a `make dist` tree (e.g. CI's vux9k-dist-<sha> artifact): nothing is rebuilt
DIST ?= $(BUILD_DIR)/dist
test-hw-dist:
	$(OPENFPGALOADER) -b tangnano9k $(DIST)/bitstream/pack.fs
	$(PYTHON) scripts/test_hardware.py --dist $(DIST)

# ===== Clean =====

clean:
	$(VERYL) clean
	cd firmware && $(CARGO) clean
	cd vendor/bc_clone_rs/crates/bc_zephyr 2>/dev/null && $(CARGO) clean || true
	$(MAKE) -C hack_demo clean
	rm -rf $(BUILD_DIR) ./firmware.hex ./firmware_d*.hex zephyr_workspace/app/build
