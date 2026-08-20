# Top-level Makefile for Tang Nano 9K Dual-ISA SoC Project

VENV_PATH ?= .venv
UV ?= uv
PYTHON ?= $(VENV_PATH)/bin/python

.PHONY: all setup firmware emu sim test clean

all: test

setup:
	$(UV) venv $(VENV_PATH)
	$(UV) pip install cocotb pytest
	rustup target add riscv32i-unknown-none-elf
	git submodule update --init --recursive

firmware:
	cd firmware && cargo build --release
	$(PYTHON) scripts/elf2bin.py firmware/target/riscv32i-unknown-none-elf/release/firmware firmware/firmware.bin
	$(PYTHON) scripts/bin2hex.py firmware/firmware.bin firmware/firmware.hex

emu: firmware
	$(PYTHON) sim/emulator.py firmware/firmware.bin

sim: firmware
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim SIM=ghdl MODULE=test_soc_rv32i
	PATH=$(PWD)/$(VENV_PATH)/bin:$(PATH) $(MAKE) -C sim SIM=ghdl MODULE=test_soc_hack

test: firmware emu sim
	@echo "================================================="
	@echo " ALL Tang Nano 9K SoC TESTS PASSED SUCCESSFULLY! "
	@echo "================================================="

clean:
	cd firmware && cargo clean
	rm -rf sim/sim_build sim/results.xml firmware/firmware.bin firmware/firmware.hex
