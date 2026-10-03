# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from unit_models import rand32


@cocotb.test()
async def test_rv32i_regfile(dut):
    """Test 32x32-bit register file with prioritized write ports and x0 wired to 0"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    dut.rst.value = 0
    dut.we.value = 0
    dut.rd_addr.value = 0
    dut.wr_data.value = 0
    dut.we2.value = 0
    dut.rd2_addr.value = 0
    dut.wr2_data.value = 0
    dut.rs1_addr.value = 0
    dut.rs2_addr.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 1)

    # 2. Write to x0 on both ports (should be discarded)
    dut.we.value = 1
    dut.rd_addr.value = 0
    dut.wr_data.value = 0xDEADBEEF
    await ClockCycles(dut.clk, 1)
    dut.we.value = 0
    dut.rs1_addr.value = 0
    await Timer(1, unit="ns")
    assert int(dut.rs1_data.value) == 0, "x0 must always be 0!"

    # 3. Port 1 writes x1 = 0x1234_5678
    dut.we.value = 1
    dut.rd_addr.value = 1
    dut.wr_data.value = 0x1234_5678
    await ClockCycles(dut.clk, 1)
    dut.we.value = 0

    # 4. Port 2 writes x2 = 0x8765_4321
    dut.we2.value = 1
    dut.rd2_addr.value = 2
    dut.wr2_data.value = 0x8765_4321
    await ClockCycles(dut.clk, 1)
    dut.we2.value = 0

    # 5. Simultaneous read back: rs1 = x1, rs2 = x2
    dut.rs1_addr.value = 1
    dut.rs2_addr.value = 2
    await Timer(1, unit="ns")
    assert int(dut.rs1_data.value) == 0x1234_5678, f"Expected 0x12345678, got {hex(int(dut.rs1_data.value))}"
    assert int(dut.rs2_data.value) == 0x8765_4321, f"Expected 0x87654321, got {hex(int(dut.rs2_data.value))}"

    dut._log.info("Register file verified successfully [PASS]")


@cocotb.test()
async def test_rv32i_regfile_random(dut):
    """Random writes on both ports against a model: port 1 wins whenever it writes (even to
    another register; port 2's write is then dropped), x0 stays 0, and both read ports see
    every register, the cycle after the write"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig in ("we", "rd_addr", "wr_data", "we2", "rd2_addr", "wr2_data", "rs1_addr", "rs2_addr"):
        getattr(dut, sig).value = 0
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1

    # Registers have no reset: give every one a known value first
    model = [0] * 32
    await FallingEdge(dut.clk)
    for r in range(1, 32):
        model[r] = rand32()
        dut.we.value = 1
        dut.rd_addr.value = r
        dut.wr_data.value = model[r]
        await FallingEdge(dut.clk)
    dut.we.value = 0

    for _ in range(2000):
        we, we2 = random.random() < 0.6, random.random() < 0.6
        rd = random.randrange(32)
        rd2 = rd if random.random() < 0.2 else random.randrange(32)
        d1, d2 = rand32(), rand32()
        rs1, rs2 = random.randrange(32), random.randrange(32)
        dut.we.value, dut.rd_addr.value, dut.wr_data.value = int(we), rd, d1
        dut.we2.value, dut.rd2_addr.value, dut.wr2_data.value = int(we2), rd2, d2
        dut.rs1_addr.value, dut.rs2_addr.value = rs1, rs2
        await Timer(1, unit="ns")  # reads are combinational: the old values before the edge
        assert int(dut.rs1_data.value) == model[rs1], f"x{rs1} before write"
        assert int(dut.rs2_data.value) == model[rs2], f"x{rs2} before write"
        await FallingEdge(dut.clk)
        if we and rd != 0:
            model[rd] = d1
        elif we2 and rd2 != 0:
            model[rd2] = d2
    dut.we.value = dut.we2.value = 0
    for r in range(32):
        dut.rs1_addr.value = r
        dut.rs2_addr.value = 31 - r
        await Timer(1, unit="ns")
        assert int(dut.rs1_data.value) == model[r], f"x{r}"
        assert int(dut.rs2_data.value) == model[31 - r], f"x{31 - r}"
