# cocotb Makefile for Tang Nano 9K Dual-ISA SoC

SIM ?= ghdl
TOPLEVEL_LANG ?= vhdl

VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/rv32i/rv32i_types.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/rv32i/rv32i_alu.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/rv32i/rv32i_decode.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/rv32i/rv32i_regfile.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/hack/alu.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/hack/decode.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/unified/auto_mode_detector.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/unified/hack_translator.vhd
VHDL_SOURCES += $(PWD)/../submodules/hack_cpu/rtl/unified/unified_cpu.vhd

VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/clk_timer.vhd
VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/fifo_sync.vhd
VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/shift_registers.vhd
VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/uart_tx.vhd
VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/uart_rx.vhd
VHDL_SOURCES += $(PWD)/../submodules/uart_controller/rtl/uart_controller.vhd

VHDL_SOURCES += $(PWD)/../soc/timer_core.vhd
VHDL_SOURCES += $(PWD)/../soc/sdcard_spi.vhd
VHDL_SOURCES += $(PWD)/../soc/soc_ram.vhd
VHDL_SOURCES += $(PWD)/../soc/soc_top.vhd

EXTRA_ARGS += --std=08
GHDL_ARGS += --std=08
SIM_ARGS += --max-stack-alloc=0

TOPLEVEL = soc_top
MODULE ?= test_soc_rv32i

include $(shell cocotb-config --makefiles)/Makefile.sim
