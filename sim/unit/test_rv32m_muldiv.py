# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""rv32m_muldiv: every operation's result against the spec (muldiv_model), and its fixed
latency checked every cycle: `start`, then 32 cycles stepping, then `done` for one cycle with
the result, which holds until the next `start`."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer
from unit_models import DIV, DIVU, EDGES32, MULHSU, REM, REMU, muldiv_model, rand32

STEPS = 32  # cycles stepping after start; done in the next one

# Division's cases the spec defines separately
SPECIAL = ("by_zero", "overflow", "other")
# (funct3, case): the overflow exists for the signed forms only
DIV_CASES = [(d, c) for d in (DIV, DIVU, REM, REMU) for c in SPECIAL if c != "overflow" or d in (DIV, REM)]


def special(funct3, a, b):
    if funct3 not in (DIV, DIVU, REM, REMU):
        return None
    if b == 0:
        return "by_zero"
    if funct3 in (DIV, REM) and a == 0x8000_0000 and b == 0xFFFF_FFFF:
        return "overflow"
    return "other"


# Every operation on every operand class, and division's special cases
@fcov.point("muldiv.op", range(8), xf=lambda f, a, b: f)
@fcov.point("muldiv.a", fcov.CLASSES32, xf=lambda f, a, b: fcov.classify32(a))
@fcov.point("muldiv.b", fcov.CLASSES32, xf=lambda f, a, b: fcov.classify32(b))
@fcov.point("muldiv.div_case", DIV_CASES, xf=lambda f, a, b: (f, special(f, a, b)) if special(f, a, b) else None)
@fcov.cross("muldiv.op_x_a", ("muldiv.op", "muldiv.a"))
@fcov.cross("muldiv.op_x_b", ("muldiv.op", "muldiv.b"))
def sample(funct3, a, b):
    pass


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.start.value = 0
    dut.funct3.value = 0
    dut.a.value = 0
    dut.b.value = 0
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await Timer(1, unit="ns")
    assert int(dut.done.value) == 0, "idle after reset"


async def begin(dut, funct3, a, b):
    """Present one operation with start for one cycle (from a falling edge)."""
    dut.start.value = 1
    dut.funct3.value = funct3
    dut.a.value = a
    dut.b.value = b
    await FallingEdge(dut.clk)
    dut.start.value = 0
    # The inputs are only sampled with start: scramble them
    dut.funct3.value = random.getrandbits(3)
    dut.a.value = random.getrandbits(32)
    dut.b.value = random.getrandbits(32)


async def check(dut, funct3, a, b):
    sample(funct3, a, b)
    await begin(dut, funct3, a, b)
    for n in range(1, STEPS + 1):
        await Timer(1, unit="ns")
        assert int(dut.done.value) == 0, f"cycle {n} after start"
        await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.done.value) == 1, "done the cycle after the last step"
    want = muldiv_model(funct3, a, b)
    got = int(dut.result.value)
    assert got == want, f"funct3 {funct3} a=0x{a:08x} b=0x{b:08x}: got 0x{got:08x}, want 0x{want:08x}"
    await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.done.value) == 0, "done lasts one cycle"
    assert int(dut.result.value) == want, "the result holds until the next start"


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_muldiv_edges(dut):
    """Every operation on every pair of boundary values (signs, zero, the overflow)"""
    await reset(dut)
    for funct3 in range(8):
        for a in EDGES32:
            for b in EDGES32:
                await check(dut, funct3, a, b)
    fcov.export()


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_muldiv_random(dut):
    """Random operands (small divisors often) for every operation"""
    await reset(dut)
    for _ in range(1500):
        b = rand32()
        if random.random() < 0.25:
            b &= 0xFF
        await check(dut, random.getrandbits(3), rand32(), b)
    fcov.export()


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_muldiv_restart(dut):
    """A start while busy restarts: the new operation's result, after the full latency"""
    await reset(dut)
    for cut in (1, 16, STEPS):
        await begin(dut, DIV, 0x1234_5678, 7)
        for _ in range(cut - 1):
            await FallingEdge(dut.clk)
        await Timer(1, unit="ns")
        assert int(dut.done.value) == 0
        await check(dut, REMU, 0xFFFF_FFFF, 10)
    # Back to back: start in the cycle done is high
    await begin(dut, MULHSU, 0xFFFF_FFFF, 0xFFFF_FFFF)
    for _ in range(STEPS):
        await FallingEdge(dut.clk)
    await Timer(1, unit="ns")
    assert int(dut.done.value) == 1
    assert int(dut.result.value) == muldiv_model(MULHSU, 0xFFFF_FFFF, 0xFFFF_FFFF)
    await check(dut, DIVU, 100, 0)
