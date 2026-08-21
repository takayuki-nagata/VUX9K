# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_fifo_sync(dut):
    """Test synchronous FIFO buffer push, pop, full, empty flags and ordering"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await Timer(1, unit="ns")

    assert int(dut.empty.value) == 1, "FIFO should be empty after reset"
    assert int(dut.full.value) == 0, "FIFO should not be full after reset"

    # 2. Write 4 items
    test_data = [0xA1, 0xB2, 0xC3, 0xD4]
    for val in test_data:
        dut.we.value = 1
        dut.wdata.value = val
        await FallingEdge(dut.clk)
    
    dut.we.value = 0
    await Timer(1, unit="ns")
    assert int(dut.empty.value) == 0, "FIFO should not be empty after writes"

    # 3. Read back items and verify FIFO order
    read_data = []
    for _ in range(len(test_data)):
        await Timer(1, unit="ns")
        read_data.append(int(dut.rdata.value))
        dut.re.value = 1
        await FallingEdge(dut.clk)

    dut.re.value = 0
    await Timer(1, unit="ns")
    assert read_data == test_data, f"Data mismatch: expected {test_data}, got {read_data}"
    assert int(dut.empty.value) == 1, "FIFO should be empty after all reads"

    # 4. Simultaneous Write & Read
    dut.we.value = 1
    dut.wdata.value = 0x55
    await FallingEdge(dut.clk) # 0x55 in FIFO
    dut.wdata.value = 0xAA
    dut.re.value = 1 # Pop 0x55 while pushing 0xAA
    await FallingEdge(dut.clk)
    dut.we.value = 0
    dut.re.value = 0
    await Timer(1, unit="ns")
    assert int(dut.rdata.value) == 0xAA, f"Expected 0xAA remaining in FIFO, got {hex(int(dut.rdata.value))}"

    dut._log.info("FIFO buffer verified successfully [PASS]")
