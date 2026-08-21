VERYL = veryl
YOSYS = yosys
GOWIN_PACK = gowin_pack
NEXTPNR = nextpnr-gowin
CARGO = cargo
PYTHON = python3
UV = uv

VENV_PATH = .venv

.PHONY: all veryl check check-paths fmt test build synth clean venv setup firmware sim-unit sim-soc sim test-arch-compliance

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
	$(VENV_PATH)/bin/python scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware firmware/firmware.bin
	$(VENV_PATH)/bin/python scripts/bin2hex.py firmware/firmware.bin firmware/firmware.hex

sim-unit: veryl
	@echo "=== Running RV32I ALU Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=rv32i_alu MODULE=test_rv32i_alu
	@echo "=== Running RV32I Decoder Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=rv32i_decode MODULE=test_rv32i_decode
	@echo "=== Running Hack Translator Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=hack_translator MODULE=test_hack_translator
	@echo "=== Running RV32I Register File Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=rv32i_regfile MODULE=test_rv32i_regfile
	@echo "=== Running RV32I CSRs & Trap Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=rv32i_csrs MODULE=test_rv32i_csrs
	@echo "=== Running Auto Mode Detector Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=auto_mode_detector MODULE=test_auto_mode_detector
	@echo "=== Running Unified Dual-ISA CPU Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_unified_cpu
	@echo "=== Running Hack CPU Comprehensive Ops Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_hack_cpu_ops
	@echo "=== Running RV32I ISA Compliance Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=unified_cpu MODULE=test_rv32i_compliance
	@echo "=== Running UART Clock Timer Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=clk_timer MODULE=test_clk_timer
	@echo "=== Running UART Shift Registers Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=shift_registers MODULE=test_shift_registers
	@echo "=== Running UART FIFO Sync Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=fifo_sync MODULE=test_fifo_sync
	@echo "=== Running UART TX Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=uart_tx MODULE=test_uart_tx
	@echo "=== Running UART RX Unit Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=uart_rx MODULE=test_uart_rx
	@echo "=== Running UART Controller Loopback Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=uart_controller MODULE=test_uart_controller

test-arch-compliance: veryl
	@echo "=== Running Official RISC-V Architectural Compliance Tests ==="
	$(VENV_PATH)/bin/python scripts/run_arch_test.py

sim-soc: firmware
	@echo "=== Running Python Software Emulator Unit Tests ==="
	$(VENV_PATH)/bin/pytest sim/test_emulator.py
	@echo "=== Running Python Software Emulator ==="
	$(VENV_PATH)/bin/python sim/emulator.py firmware/firmware.bin
	@echo "=== Running SoC Top RISC-V Integration Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_rv32i
	@echo "=== Running SoC Top Hack Integration Tests ==="
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim TOPLEVEL=soc_top MODULE=test_soc_hack

sim: sim-unit test-arch-compliance sim-soc

synth: veryl
	$(YOSYS) -p "\
		read_verilog -sv cpu/rv32i_pkg.sv cpu/auto_mode_detector.sv cpu/hack_translator.sv cpu/rv32i_alu.sv cpu/rv32i_decode.sv cpu/rv32i_regfile.sv cpu/rv32i_csrs.sv cpu/unified_cpu.sv uart/*.sv soc/*.sv; \
		synth_gowin -top soc_top -json soc.json; \
	"

test: check firmware sim synth
	@echo "========================================================================"
	@echo "  ALL VERYL CPU, UART, ARCH-COMPLIANCE & SOC TESTS PASSED 100%!         "
	@echo "========================================================================"

clean:
	$(VERYL) clean
	cd firmware && $(CARGO) clean
	rm -rf sim/sim_build* sim/results.xml soc.json pack.fs firmware/firmware.bin firmware/firmware.hex build_arch_test
