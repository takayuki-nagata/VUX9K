# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
gpio_controller unit tests: active-low LEDs, synchronized active-low button, and the
magic-value soft-reset pulse.

Register map (addr[3:0]): 0x0 LEDs (6 bits, rw), 0x4 button (1 = pressed, ro),
0x8 boot mode for the next soft reset (bit 8 valid, bit 0 RV32; valid clears when the
reset pulse ends), 0xC soft reset (write 0x5A5AA55A or 0x0000A55A to pulse cpu_soft_rst; reads 1 while
the pulse is active). Other offsets read 0. Reads are registered (one cycle).
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from unit_models import rand32

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
    """Offsets other than 0x0/0x4/0x8/0xC read 0, and writes to them change nothing"""
    await setup(dut)
    await write(dut, 0x0, 0x11)
    await write(dut, 0x9, 0x3F)
    for addr in (0x1, 0x2, 0x9, 0xF):
        assert await read(dut, addr) == 0, f"offset 0x{addr:X} must read 0"
    assert await read(dut, 0x0) == 0x11, "write to offset 0x9 changed the LEDs"
    assert int(dut.cpu_soft_rst.value) == 0


@cocotb.test()
async def test_boot_mode_register(dut):
    """0x8 holds the ISA for the next soft reset; it reads back, and valid clears when the pulse ends"""
    await setup(dut)
    assert (int(dut.boot_mode_valid.value), await read(dut, 0x8)) == (0, 0), "reset value"
    await write(dut, 0x8, 0x100)  # valid, Hack
    assert await read(dut, 0x8) == 0x100
    assert (int(dut.boot_mode_valid.value), int(dut.boot_mode.value)) == (1, 0)
    await write(dut, 0x8, 0x101)  # valid, RV32
    assert (int(dut.boot_mode_valid.value), int(dut.boot_mode.value)) == (1, 1)

    await write(dut, 0xC, MAGIC_A)
    n = 0
    while int(dut.cpu_soft_rst.value) == 1 and n < 64:
        assert int(dut.boot_mode_valid.value) == 1, "valid must hold for the whole reset pulse"
        await FallingEdge(dut.clk)
        n += 1
    assert n == PULSE_CYCLES
    assert int(dut.boot_mode_valid.value) == 0, "valid must clear once the soft reset used it"
    assert await read(dut, 0x8) == 0x001, "the mode bit itself is kept"


class GpioModel:
    """gpio_controller per rising edge (reads show the registers before the edge). Any
    write, even to another register, holds the soft-reset pulse counter for that cycle."""

    def __init__(self):
        self.led, self.cnt, self.valid, self.rv32, self.sync = 0, 0, 0, 0, 0b11

    def edge(self, we, addr, data, btn):
        pressed = 1 - (self.sync >> 1)
        out = {0x0: self.led, 0x4: pressed, 0x8: self.valid << 8 | self.rv32, 0xC: int(self.cnt != 0)}.get(addr, 0)
        if we:
            if addr == 0x0:
                self.led = data & 0x3F
            elif addr == 0x8:
                self.valid, self.rv32 = data >> 8 & 1, data & 1
            elif addr == 0xC and data in (MAGIC_A, MAGIC_B):
                self.cnt = PULSE_CYCLES
        elif self.cnt:
            if self.cnt == 1:
                self.valid = 0
            self.cnt -= 1
        self.sync = (self.sync << 1 | btn) & 0b11
        return out


@cocotb.test()
async def test_random_against_model(dut):
    """Random writes/reads of every offset, near-miss magic values, boot-mode writes during
    a pulse and button changes, checked against GpioModel every cycle"""
    await setup(dut)
    model = GpioModel()
    for _ in range(3000):
        addr = random.choice((0x0, 0x4, 0x8, 0xC)) if random.random() < 0.85 else random.randrange(16)
        we = random.random() < 0.3
        magic = random.choice((MAGIC_A, MAGIC_B))
        data = random.choice((magic, magic ^ 1 << random.randrange(32), rand32(), 0x101, 0x100, 0x1))
        btn = int(random.random() < 0.9) if random.random() < 0.95 else random.getrandbits(1)
        dut.we.value, dut.addr.value, dut.data_in.value, dut.gpio_btn.value = int(we), addr, data, btn
        out = model.edge(we, addr, data, btn)
        await FallingEdge(dut.clk)
        ctx = f"we={int(we)} addr=0x{addr:x} data=0x{data:08x}"
        assert int(dut.data_out.value) == out, f"data_out 0x{int(dut.data_out.value):x} != 0x{out:x}, {ctx}"
        assert int(dut.gpio_led.value) == model.led ^ 0x3F, f"gpio_led, {ctx}"
        assert int(dut.cpu_soft_rst.value) == int(model.cnt != 0), f"cpu_soft_rst, {ctx}"
        assert int(dut.boot_mode_valid.value) == model.valid, f"boot_mode_valid, {ctx}"
        assert int(dut.boot_mode.value) == model.rv32, f"boot_mode, {ctx}"
