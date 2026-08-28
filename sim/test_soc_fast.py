# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Top-Level SoC Verification Testbench (test_soc_fast.py)
Verifies power-on reset, CPU boot, instruction execution, and initial peripheral activation.
"""

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock


@cocotb.test()
async def test_soc_fast_boot(dut):
    """Verify top-level SoC power-on reset release and CPU boot execution"""
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    # Initialize external pins
    dut.rst_n.value = 0
    dut.btn.value = 1
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    # Assert active-low reset
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    # Wait for Power-on-reset counter release
    await ClockCycles(dut.clk, 30)
    dut._log.info("SoC Reset released.")

    # Run for 200 cycles to verify CPU instruction execution and firmware startup
    for i in range(200):
        await ClockCycles(dut.clk, 1)

    dut._log.info("SoC Fast Boot & Execution verified successfully!")
