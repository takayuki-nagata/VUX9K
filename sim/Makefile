# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# cocotb Makefile for Tang Nano 9K Dual-ISA SoC (100% Veryl)

# Dynamically discover runtime libraries and Python configuration across Linux environments
export LD_PRELOAD := $(shell gcc -print-file-name=librt.so.1 2>/dev/null) $(shell gcc -print-file-name=libutil.so.1 2>/dev/null)
export LIBPYTHON_LOC := $(shell cocotb-config --libpython 2>/dev/null)
export PYTHONHOME := $(shell python3 -c 'import sys; print(sys.base_prefix)' 2>/dev/null)
export PYTHONPATH := $(shell python3 -c 'import sys; print(":".join(sys.path))' 2>/dev/null):$(CURDIR)

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ifeq ($(SIM_GLS),1)
# Gate-Level Simulation (GLS) using Gowin primitive simulation models + Synthesized Netlist
TOPLEVEL ?= soc_top
SYN_NETLIST ?= $(if $(filter soc_top,$(TOPLEVEL)),$(CURDIR)/../soc_syn.v,$(CURDIR)/../$(TOPLEVEL)_syn.v)
VERILOG_SOURCES = $(CURDIR)/gowin_cells_sim.v $(SYN_NETLIST)
COMPILE_ARGS = -g2012
MODULE ?= test_soc_fast
SIM_BUILD = sim_build_gls_$(TOPLEVEL)
else
# RTL Behavioral Simulation
VERILOG_SOURCES += $(CURDIR)/../cpu/rv32i_pkg.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/rv32i_alu.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/rv32i_decode.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/rv32i_regfile.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/rv32i_csrs.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/hack_translator.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/auto_mode_detector.sv
VERILOG_SOURCES += $(CURDIR)/../cpu/unified_cpu.sv

VERILOG_SOURCES += $(CURDIR)/../uart/clk_timer.sv
VERILOG_SOURCES += $(CURDIR)/../uart/shift_registers.sv
VERILOG_SOURCES += $(CURDIR)/../uart/fifo_sync.sv
VERILOG_SOURCES += $(CURDIR)/../uart/uart_tx.sv
VERILOG_SOURCES += $(CURDIR)/../uart/uart_rx.sv
VERILOG_SOURCES += $(CURDIR)/../uart/uart_controller.sv

VERILOG_SOURCES += $(CURDIR)/../soc/timer_core.sv
VERILOG_SOURCES += $(CURDIR)/../soc/sdcard_spi.sv
VERILOG_SOURCES += $(CURDIR)/../soc/gpio_controller.sv
VERILOG_SOURCES += $(CURDIR)/../soc/hw_boot_mgr.sv
VERILOG_SOURCES += $(CURDIR)/../soc/soc_ram.sv
VERILOG_SOURCES += $(CURDIR)/../soc/soc_top.sv

COMPILE_ARGS += -g2012
TOPLEVEL ?= soc_top
MODULE ?= test_soc_rv32i
SIM_BUILD ?= sim_build_$(TOPLEVEL)
endif

include $(shell cocotb-config --makefiles)/Makefile.sim
