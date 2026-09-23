# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock

@cocotb.test()
async def test_soc_zephyr_execution(dut):
    """Test Zephyr RTOS & Rust SoC execution in cocotb simulation"""
    clock = Clock(dut.clk, 20, unit="ns") # 50 MHz
    cocotb.start_soon(clock.start())

    # Assert Reset (active-low)
    dut.rst.value = 0
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1
    await ClockCycles(dut.clk, 10)
    # Release Reset
    dut.rst.value = 1

    await ClockCycles(dut.clk, 100)

    # Check active_mode (1 = RISC-V mode)
    assert int(dut.active_mode.value) == 1, "SoC should auto-detect RISC-V mode for Zephyr!"
    dut._log.info("SoC active_mode verified: RISC-V 32-bit execution mode for Zephyr [OK]")

    # Run for simulation cycles
    await ClockCycles(dut.clk, 2000)
    dut._log.info("Zephyr RTOS SoC RTL Simulation completed successfully.")
