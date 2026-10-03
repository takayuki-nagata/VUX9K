# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge


@cocotb.test()
async def test_clk_timer(dut):
    """Test clk_timer periodic alarm pulse generation"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.clr.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)

    # 2. Count alarm pulses over cycles
    # Default CNT in clk_timer is 434 (Tang Nano 9K 50MHz / 115200 baud)
    # Let's count for 434 * 3 cycles -> should get exactly 3 pulses
    alm_count = 0
    total_cycles = 434 * 3
    for _ in range(total_cycles):
        await FallingEdge(dut.clk)
        if int(dut.alm.value) == 1:
            alm_count += 1

    assert alm_count == 3, f"Expected 3 alarm pulses, got {alm_count}"
    dut._log.info(f"clk_timer verified successfully: received {alm_count} pulses in {total_cycles} cycles [PASS]")


@cocotb.test()
async def test_clk_timer_clr_restarts_period(dut):
    """A one-cycle clr restarts the period: the next alarm comes a full CNT edges later, none earlier"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.clr.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    cnt = 434  # clk_timer's default CNT

    # Get to a few cycles before an alarm is due, then restart the period
    while int(dut.alm.value) == 0:
        await FallingEdge(dut.clk)
    for _ in range(cnt - 5):
        await FallingEdge(dut.clk)
    dut.clr.value = 1
    await FallingEdge(dut.clk)
    dut.clr.value = 0
    edges = 1
    while int(dut.alm.value) == 0:
        await FallingEdge(dut.clk)
        edges += 1
    assert edges == cnt, f"alarm {edges} edges after clr, expected a full period ({cnt})"


@cocotb.test()
async def test_clk_timer_random_clr(dut):
    """Random clr pulses (incl. on the alarm cycle and back to back) against a counter
    model: alm exactly when the model's counter is 0, every cycle"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.clr.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    cnt = 434  # clk_timer's default CNT
    counter = cnt - 1
    for _ in range(5 * cnt):
        assert int(dut.alm.value) == (counter == 0), f"alm with the counter at {counter}"
        clr = random.random() < (0.5 if counter < 3 else 0.004)  # often right at the alarm
        dut.clr.value = int(clr)
        await FallingEdge(dut.clk)
        counter = cnt - 1 if clr or counter == 0 else counter - 1
