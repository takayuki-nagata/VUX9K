# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
timer_core (machine timer: 64-bit mtime / mtimecmp, timer_irq) unit tests.

Register map (addr[3:0]): 0x0/0x4 mtime lo/hi, 0x8/0xC mtimecmp lo/hi; others read 0.
Reads are registered: data_out reflects the addressed register at the previous
rising edge.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from unit_models import rand32

ALL_ONES = 0xFFFFFFFF


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.data_in.value = 0
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
    """Value of the register at addr as of the rising edge before the returned falling edge."""
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    await FallingEdge(dut.clk)
    return int(dut.data_out.value)


@cocotb.test()
async def test_reset_values(dut):
    """After reset mtimecmp is all-ones and no interrupt is pending"""
    await setup(dut)
    assert await read(dut, 0x8) == ALL_ONES
    assert await read(dut, 0xC) == ALL_ONES
    assert int(dut.timer_irq.value) == 0
    assert await read(dut, 0x4) == 0, "mtime hi must start at 0"


@cocotb.test()
async def test_mtime_counts_every_cycle(dut):
    """mtime advances by exactly one per clock"""
    await setup(dut)
    t0 = await read(dut, 0x0)
    await ClockCycles(dut.clk, 99, rising=False)  # read() itself spans one more edge
    t1 = await read(dut, 0x0)
    assert t1 - t0 == 101, f"mtime advanced {t1 - t0} in 101 cycles"


@cocotb.test()
async def test_mtime_write_and_carry(dut):
    """A written low word wins over the increment, and the low word carries into the high word"""
    await setup(dut)
    await write(dut, 0x4, 7)
    await write(dut, 0x0, 0xFFFFFFF0)
    lo = await read(dut, 0x0)
    assert 0xFFFFFFF0 <= lo < 0xFFFFFFF8, f"low word after write: 0x{lo:08X}"
    assert await read(dut, 0x4) == 7
    await ClockCycles(dut.clk, 20)
    assert await read(dut, 0x4) == 8, "low-word overflow must carry into the high word"
    assert await read(dut, 0x0) < 0x40


@cocotb.test()
async def test_mtimecmp_readback_and_unmapped(dut):
    """mtimecmp halves read back independently; unmapped offsets read 0 and ignore writes"""
    await setup(dut)
    await write(dut, 0x8, 0x12345678)
    await write(dut, 0xC, 0x9ABCDEF0)
    assert await read(dut, 0x8) == 0x12345678
    assert await read(dut, 0xC) == 0x9ABCDEF0
    await write(dut, 0x2, 0xDEADBEEF)
    for addr in (0x1, 0x2, 0x3, 0x5, 0x9, 0xE):
        assert await read(dut, addr) == 0, f"unmapped offset 0x{addr:X} must read 0"
    assert await read(dut, 0x8) == 0x12345678, "write to an unmapped offset changed mtimecmp"


@cocotb.test()
async def test_timer_irq_compare(dut):
    """timer_irq rises once mtime >= mtimecmp (unsigned 64-bit) and clears when mtimecmp moves up"""
    await setup(dut)
    await write(dut, 0xC, 0)  # hi first: mtimecmp = 0x00000000_FFFFFFFF -> still far away
    now = await read(dut, 0x0)
    target = now + 40
    await write(dut, 0x8, target)
    await ClockCycles(dut.clk, 20)
    assert int(dut.timer_irq.value) == 0, "irq asserted before mtime reached mtimecmp"
    await ClockCycles(dut.clk, 30)
    assert int(dut.timer_irq.value) == 1, "irq not asserted after mtime passed mtimecmp"
    await ClockCycles(dut.clk, 10)
    assert int(dut.timer_irq.value) == 1, "irq must stay asserted while mtime >= mtimecmp"
    await write(dut, 0xC, 1)  # mtimecmp now 2^32 above mtime
    await ClockCycles(dut.clk, 2)
    assert int(dut.timer_irq.value) == 0, "irq must clear after mtimecmp is moved past mtime"


@cocotb.test()
async def test_mtime_low_write_does_not_carry(dut):
    """Writing mtime's low word while it is 0xFFFFFFFF leaves the high word alone.

    The write and that edge's +1 land on the same edge: the written low word wins,
    and the increment's carry must not leak into the high word.
    """
    await setup(dut)
    await write(dut, 0x4, 7)
    await write(dut, 0x0, ALL_ONES - 1)  # the next edge (between writes) makes lo 0xFFFFFFFF
    await write(dut, 0x0, 0x100)  # this edge would carry lo 0xFFFFFFFF -> 0 into hi
    assert await read(dut, 0x4) == 7, "low-word write must not carry into the high word"
    lo = await read(dut, 0x0)
    assert 0x100 <= lo < 0x108, f"low word after write: 0x{lo:08X}"


class TimerModel:
    """timer_core per rising edge: reads and timer_irq show the registers before the edge;
    mtime counts every edge, and a write to a half replaces that half (a low write also
    drops that edge's +1, a high write keeps the low half's +1 but not its carry)."""

    def __init__(self, mtime, mtimecmp):
        self.mtime, self.mtimecmp = mtime, mtimecmp

    def edge(self, we, addr, data):
        m, c = self.mtime, self.mtimecmp
        out = {0x0: m & ALL_ONES, 0x4: m >> 32, 0x8: c & ALL_ONES, 0xC: c >> 32}.get(addr, 0)
        irq = int(m >= c)
        m_next = (m + 1) & (1 << 64) - 1
        if we and addr == 0x0:
            m_next = m & ~ALL_ONES | data
        elif we and addr == 0x4:
            m_next = data << 32 | m_next & ALL_ONES
        elif we and addr == 0x8:
            c = c & ~ALL_ONES | data
        elif we and addr == 0xC:
            c = data << 32 | c & ALL_ONES
        self.mtime, self.mtimecmp = m_next, c
        return out, irq


@cocotb.test()
async def test_random_against_model(dut):
    """Random reads and writes of every offset (unmapped ones too), with values aimed at
    the low word's carry and at mtimecmp just around mtime, checked every cycle"""
    await setup(dut)
    # Known state: write mtime hi, then lo (the lo write drops that edge's increment)
    await write(dut, 0x4, 0)
    await write(dut, 0x0, 0)
    await write(dut, 0x8, ALL_ONES)
    await write(dut, 0xC, ALL_ONES)
    # write() waits for a falling edge, writes on the next rising one and returns at the
    # falling edge after it: each mtimecmp write takes two edges, so mtime is 4 by now
    model = TimerModel(4, (1 << 64) - 1)

    for _ in range(4000):
        r = random.random()
        addr = random.choice((0x0, 0x4, 0x8, 0xC)) if r < 0.85 else random.randrange(16)
        we = random.random() < 0.3
        m = model.mtime
        if random.random() < 0.6:  # aim near the interesting values
            data = (
                random.choice(
                    (
                        (m & ALL_ONES) + random.randint(-3, 3),
                        (m >> 32) + random.randint(-1, 1),
                        ALL_ONES - random.randint(0, 6),
                        random.randint(0, 3),
                    )
                )
                & ALL_ONES
            )
        else:
            data = rand32()
        dut.we.value, dut.addr.value, dut.data_in.value = int(we), addr, data
        out, irq = model.edge(we, addr, data)
        await FallingEdge(dut.clk)
        ctx = f"we={int(we)} addr=0x{addr:x} data=0x{data:08x}"
        assert int(dut.data_out.value) == out, f"data_out 0x{int(dut.data_out.value):08x} != 0x{out:08x}, {ctx}"
        assert int(dut.timer_irq.value) == irq, f"timer_irq, {ctx}"
