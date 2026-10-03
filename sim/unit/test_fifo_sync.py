# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random
from collections import deque

import cocotb
import fcov
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


# Every we/re combination with the FIFO empty, partly filled and full (the doc string's
# claim, counted): a write when full is accepted only with a read, a read when empty is none
@fcov.point("fifo.we_re", ((0, 0), (0, 1), (1, 0), (1, 1)), xf=lambda we, re, level: (we, re))
@fcov.point("fifo.level", ("empty", "partial", "full"), xf=lambda we, re, level: level)
@fcov.cross("fifo.we_re_x_level", ("fifo.we_re", "fifo.level"))
def sample(we, re, level):
    pass


@cocotb.test()
async def test_fifo_random(dut):
    """Random writes and reads against a deque, in phases that fill the FIFO up to full and
    drain it to empty, so both edges see every we/re combination; rdata, full and empty
    are checked every cycle"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    depth = 1 << 8  # fifo_sync's default LOG_DEPTH (parameters aren't VPI-visible under Verilator)
    model: deque[int] = deque()
    width_mask = (1 << len(dut.wdata)) - 1
    history: deque[tuple[int, int]] = deque(maxlen=8)  # the last cycles' (we, re), for failure messages
    for p_we, p_re in ((0.9, 0.2), (0.2, 0.9), (0.95, 0.5), (0.5, 0.95), (0.5, 0.5)) * 2:
        for _ in range(depth * 2):
            we, re = random.random() < p_we, random.random() < p_re
            data = random.getrandbits(32) & width_mask
            dut.we.value, dut.re.value, dut.wdata.value = int(we), int(re), data
            await Timer(1, unit="ns")
            ctx = f"{len(model)} entries; last (we, re): {list(history)}"
            assert int(dut.empty.value) == (len(model) == 0), f"empty={int(dut.empty.value)} with {ctx}"
            assert int(dut.full.value) == (len(model) == depth), f"full={int(dut.full.value)} with {ctx}"
            if model:
                assert int(dut.rdata.value) == model[0], f"rdata is not the head, {ctx}"
            history.append((int(we), int(re)))
            sample(int(we), int(re), "empty" if not model else "full" if len(model) == depth else "partial")
            do_read = re and len(model) > 0
            if we and (len(model) < depth or do_read):
                model.append(data)
            if do_read:
                model.popleft()
            await FallingEdge(dut.clk)
    fcov.export()
