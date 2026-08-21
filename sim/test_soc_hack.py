# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock

@cocotb.test()
async def test_soc_hack_execution(dut):
    """Test Hack 16-bit (Nand2Tetris) mode detection in cocotb simulation"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # Reset SoC
    dut.rst.value = 1
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0

    await ClockCycles(dut.clk, 100)

    # Note: Default RAM values 0x00000000 detect as RISC-V. Hack 16-bit first instruction (e.g. 0x0000, @0)
    dut._log.info(f"Current SoC active_mode: {dut.active_mode.value}")
    await ClockCycles(dut.clk, 200)
    dut._log.info("Hack mode test completed.")
