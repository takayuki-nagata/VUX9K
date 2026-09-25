# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer


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
    await FallingEdge(dut.clk)  # 0x55 in FIFO
    dut.wdata.value = 0xAA
    dut.re.value = 1  # Pop 0x55 while pushing 0xAA
    await FallingEdge(dut.clk)
    dut.we.value = 0
    dut.re.value = 0
    await Timer(1, unit="ns")
    assert int(dut.rdata.value) == 0xAA, f"Expected 0xAA remaining in FIFO, got {hex(int(dut.rdata.value))}"

    dut._log.info("FIFO buffer verified successfully [PASS]")


@cocotb.test()
async def test_fifo_write_and_read_while_empty(dut):
    """A write arriving together with a read on an empty FIFO is kept (the read has nothing to take)"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await Timer(1, unit="ns")
    assert int(dut.empty.value) == 1

    dut.wdata.value = 0x5A
    dut.we.value = 1
    dut.re.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0
    dut.re.value = 0
    await Timer(1, unit="ns")
    assert int(dut.empty.value) == 0, "the write was dropped"
    assert int(dut.rdata.value) == 0x5A, f"expected 0x5A at the head, got {int(dut.rdata.value):#x}"

    # ...and it is the only entry
    dut.re.value = 1
    await FallingEdge(dut.clk)
    dut.re.value = 0
    await Timer(1, unit="ns")
    assert int(dut.empty.value) == 1, "expected exactly one entry"


@cocotb.test()
async def test_fifo_full_write_and_read(dut):
    """When full, a write is refused alone but accepted together with a read, keeping FIFO order"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    depth = 0
    dut.we.value = 1
    while True:
        await Timer(1, unit="ns")
        if int(dut.full.value):
            break
        dut.wdata.value = depth & 0xFF
        await FallingEdge(dut.clk)
        depth += 1
    dut.wdata.value = 0xEE  # refused: full and no read
    await FallingEdge(dut.clk)
    dut.wdata.value = 0xDD  # accepted: a read frees the head in the same cycle
    dut.re.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0
    await Timer(1, unit="ns")
    assert int(dut.full.value) == 1, "a write + read on a full FIFO must leave it full"

    expected = [i & 0xFF for i in range(1, depth)] + [0xDD]
    got = []
    for _ in range(depth):
        await Timer(1, unit="ns")
        got.append(int(dut.rdata.value))
        await FallingEdge(dut.clk)
    dut.re.value = 0
    await Timer(1, unit="ns")
    assert got == expected, f"order broken: first {got[:3]}..., last {got[-3:]}; expected ...{expected[-3:]}"
    assert int(dut.empty.value) == 1
