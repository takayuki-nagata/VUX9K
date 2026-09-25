# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
gpio_controller unit tests: active-low LEDs, synchronized active-low button, and the
magic-value soft-reset pulse.

Register map (addr[3:0]): 0x0 LEDs (6 bits, rw), 0x4 button (1 = pressed, ro),
0xC soft reset (write 0x5A5AA55A or 0x0000A55A to pulse cpu_soft_rst; reads 1 while
the pulse is active). Other offsets read 0. Reads are registered (one cycle).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

MAGIC_A = 0x5A5AA55A
MAGIC_B = 0x0000A55A
PULSE_CYCLES = 15


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.data_in.value = 0
    dut.gpio_btn.value = 1  # released (active-low)
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst.value = 1


async def write(dut, addr, value):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    dut.data_in.value = value
    dut.we.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0


async def read(dut, addr):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    await FallingEdge(dut.clk)
    return int(dut.data_out.value)


async def pulse_length(dut, max_cycles=64):
    """Count consecutive cycles cpu_soft_rst is high, starting now."""
    n = 0
    while int(dut.cpu_soft_rst.value) == 1 and n < max_cycles:
        await FallingEdge(dut.clk)
        n += 1
    return n


@cocotb.test()
async def test_leds(dut):
    """LED register drives the pins active-low, reads back, and keeps only 6 bits"""
    await setup(dut)
    assert int(dut.gpio_led.value) == 0x3F, "all LEDs must be off (pins high) after reset"
    assert await read(dut, 0x0) == 0
    await write(dut, 0x0, 0x2A)
    assert int(dut.gpio_led.value) == 0x15, f"pins 0b{int(dut.gpio_led.value):06b} for LED value 0x2A"
    assert await read(dut, 0x0) == 0x2A
    await write(dut, 0x0, 0xFFFFFFC5)
    assert await read(dut, 0x0) == 0x05, "bits above 5 must be dropped"
    assert int(dut.cpu_soft_rst.value) == 0, "LED writes must not trigger a soft reset"


@cocotb.test()
async def test_button_sync(dut):
    """Active-low button reads 1 while pressed, through a 2-flop synchronizer"""
    await setup(dut)
    assert await read(dut, 0x4) == 0
    await FallingEdge(dut.clk)
    dut.addr.value = 0x4
    dut.gpio_btn.value = 0  # press
    seen = []
    for _ in range(5):
        await FallingEdge(dut.clk)
        seen.append(int(dut.data_out.value))
    assert seen[0] == 0, "button must not appear before the synchronizer delay"
    assert seen[-1] == 1, f"pressed button never read as 1: {seen}"
    assert seen.index(1) <= 3, f"button took {seen.index(1)} cycles to appear: {seen}"
    dut.gpio_btn.value = 1
    await ClockCycles(dut.clk, 5)
    assert await read(dut, 0x4) == 0, "released button must read 0"


@cocotb.test()
async def test_soft_reset_magic(dut):
    """Only the two magic values start a 15-cycle cpu_soft_rst pulse"""
    await setup(dut)
    for value in (0x12345678, 0x5A5A0000, 0x0000A55B, 0xA55A0000):
        await write(dut, 0xC, value)
        assert int(dut.cpu_soft_rst.value) == 0, f"0x{value:08X} must not trigger a soft reset"

    for magic in (MAGIC_A, MAGIC_B):
        await write(dut, 0xC, magic)
        # The pulse starts at the write's rising edge; write() returns half a cycle later,
        # so counting falling edges from here sees every pulse cycle exactly once.
        n = await pulse_length(dut)
        assert n == PULSE_CYCLES, f"0x{magic:08X}: pulse lasted {n} cycles, expected {PULSE_CYCLES}"
        await ClockCycles(dut.clk, 5)
        assert int(dut.cpu_soft_rst.value) == 0

    await write(dut, 0xC, MAGIC_B)
    assert await read(dut, 0xC) == 1, "offset 0xC must read 1 while the pulse is active"
    await ClockCycles(dut.clk, PULSE_CYCLES + 2)
    assert await read(dut, 0xC) == 0


@cocotb.test()
async def test_unmapped_offsets(dut):
    """Offsets other than 0x0/0x4/0xC read 0, and writes to them change nothing"""
    await setup(dut)
    await write(dut, 0x0, 0x11)
    await write(dut, 0x8, 0x3F)
    for addr in (0x1, 0x2, 0x8, 0xF):
        assert await read(dut, addr) == 0, f"offset 0x{addr:X} must read 0"
    assert await read(dut, 0x0) == 0x11, "write to offset 0x8 changed the LEDs"
    assert int(dut.cpu_soft_rst.value) == 0
