# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_shift_registers(dut):
    """Test shift_registers parallel load and serial right shifting"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Parallel Load 10'b1010010111 (0x297)
    await FallingEdge(dut.clk)
    dut.ce.value = 1
    dut.set.value = 1
    dut.pin.value = 0x297 # 10 bits: 10_1001_0111
    dut.sin.value = 1

    await FallingEdge(dut.clk)
    dut.set.value = 0
    await Timer(1, unit="ns")

    assert int(dut.pout.value) == 0x297, f"Expected 0x297, got {hex(int(dut.pout.value))}"
    assert int(dut.sout.value) == 1, "LSB should be 1"

    # 2. Shift Right by 1 with sin = 0
    # Expected: (0x297 >> 1) | (0 << 9) = 0x14B (10'b0101001011)
    dut.sin.value = 0
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.pout.value) == 0x14B, f"Expected 0x14B, got {hex(int(dut.pout.value))}"
    assert int(dut.sout.value) == 1

    # 3. Shift Right by 1 with sin = 1
    # Expected: (0x14B >> 1) | (1 << 9) = 0x2A5 (10'b1010100101)
    dut.sin.value = 1
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.pout.value) == 0x2A5, f"Expected 0x2A5, got {hex(int(dut.pout.value))}"
    assert int(dut.sout.value) == 1

    # 4. Disable clock enable (ce = 0), register contents should freeze
    dut.ce.value = 0
    dut.sin.value = 0
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.pout.value) == 0x2A5, "Contents changed while ce=0!"

    dut._log.info("Shift registers verified successfully [PASS]")
