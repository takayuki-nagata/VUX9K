# Top-level Makefile for Tang Nano 9K Dual-ISA SoC Project (VUX9K)

VENV_PATH ?= .venv
UV ?= uv
PYTHON ?= $(VENV_PATH)/bin/python
YOSYS ?= yosys

.PHONY: all setup veryl firmware emu sim synth test clean

all: test

setup:
	$(UV) venv $(VENV_PATH)
	$(UV) pip install cocotb pytest
	rustup target add riscv32i-unknown-none-elf
	git submodule update --init --recursive

veryl:
	veryl check
	veryl fmt --check
	veryl build
	veryl test

firmware:
	cd firmware && cargo build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware firmware/firmware.bin
	$(PYTHON) scripts/bin2hex.py firmware/firmware.bin firmware/firmware.hex

emu: firmware
	$(PYTHON) sim/emulator.py firmware/firmware.bin

sim: firmware
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim SIM=ghdl MODULE=test_soc_rv32i
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim SIM=ghdl MODULE=test_soc_hack

synth: veryl
	$(YOSYS) -m ghdl -p "\
		ghdl --std=08 soc/soc_top.vhd soc/soc_ram.vhd soc/timer_core.vhd soc/sdcard_spi.vhd \
			submodules/hack_cpu/rtl/rv32i/rv32i_types.vhd submodules/hack_cpu/rtl/rv32i/rv32i_alu.vhd \
			submodules/hack_cpu/rtl/rv32i/rv32i_decode.vhd submodules/hack_cpu/rtl/rv32i/rv32i_regfile.vhd \
			submodules/hack_cpu/rtl/hack/alu.vhd submodules/hack_cpu/rtl/hack/decode.vhd \
			submodules/hack_cpu/rtl/unified/auto_mode_detector.vhd submodules/hack_cpu/rtl/unified/hack_translator.vhd \
			submodules/hack_cpu/rtl/unified/unified_cpu.vhd \
			submodules/uart_controller/rtl/clk_timer.vhd submodules/uart_controller/rtl/fifo_sync.vhd \
			submodules/uart_controller/rtl/shift_registers.vhd submodules/uart_controller/rtl/uart_tx.vhd \
			submodules/uart_controller/rtl/uart_rx.vhd submodules/uart_controller/rtl/uart_controller.vhd -e soc_top; \
		read_verilog -sv soc/timer_core.sv soc/sdcard_spi.sv; \
		synth_gowin -top soc_top -json soc.json; \
	"

test: veryl firmware emu sim synth
	@echo "================================================="
	@echo " ALL Tang Nano 9K SoC TESTS PASSED SUCCESSFULLY! "
	@echo "================================================="

clean:
	cd firmware && cargo clean
	rm -rf sim/sim_build sim/results.xml firmware/firmware.bin firmware/firmware.hex soc.json
