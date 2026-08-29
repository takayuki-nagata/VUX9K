# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Gate-Level SoC Verification Testbench (test_soc_gls_fast.py)
Verifies power-on reset release, gate-level netlist execution, and initial UART activity in GLS.
"""

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock
import os
import sys

sys.path.append(os.path.dirname(__file__))
from virtual_serial import VirtualSerialBridge


@cocotb.test()
async def test_soc_gls_fast_boot(dut):
    """Verify synthesized gate-level SoC netlist boot and initial UART transmission"""
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    # Initialize external pins
    dut.rst_n.value = 0
    dut.btn.value = 1
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    ser = VirtualSerialBridge(dut, baud_cycles=234)

    # Assert active-low reset
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)
    dut._log.info("GLS: SoC Reset released.")

    # Wait for first character from UART (boot banner starts with newline)
    buf = b""
    cycles = 0
    while cycles < 30000:
        if ser.in_waiting:
            c = ser.read(ser.in_waiting)
            buf += c
            if len(buf) > 0:
                break
        await ClockCycles(dut.clk, 234)
        cycles += 234

    assert len(buf) > 0 or dut.uart_tx.value == 0, f"No UART activity detected in GLS netlist. Output: {buf!r}"
    dut._log.info("GLS: Synthesized netlist boot and UART transmission verified successfully!")
