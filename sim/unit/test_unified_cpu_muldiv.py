# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu RV32M as a program sees it: every operation's result through the whole CPU
(rd = rs1, rd = x0), the instruction's fixed cost and its single minstret count, and an
interrupt that arrives while an M instruction is fetched (it traps instead; rd unchanged).
rv32m_muldiv has its own unit test against the spec; these check the CPU's wiring
(MULDIV_WAIT, the write-back, the retire count, the trap gating). Each program checks the
values itself and spins at "ok" (or "bad"; mtvec points there unless the test takes
interrupts). They use only the CPU's ports, like test_unified_cpu_traps.
"""

import cocotb
from cocotb.clock import Clock
from rv32_asm import Asm
from test_unified_cpu_traps import expect_ok, run_program
from unit_models import muldiv_model

# An M instruction: FETCH, EXECUTE, then rv32m_muldiv's 32 steps and its done cycle
MULDIV_CYCLES = 35
PAIRS = ((7, 2), (-7, 2), (-7, -2), (-(1 << 31), -1), (5, 0), (-7, 0), (-1, -1), (0x7FFF_FFFF, 0x7FFF_FFFF))


def program(body):
    a = Asm()
    a.la("t0", "bad")
    a.csrw("mtvec", "t0")
    body(a)
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels


def expect(a, reg, value):
    a.li("t6", value)
    a.bne(reg, "t6", "bad")


@cocotb.test()
async def test_muldiv_results(dut):
    """Every operation on boundary pairs, rd = rs1 (the operands are taken at the start)
    and rd = x0 (no write)"""

    def body(a):
        for x, y in PAIRS:
            a.li("a0", x)
            a.li("a1", y)
            for f3, op in enumerate(Asm.MUL_OPS):
                a.muldiv(op, "a2", "a0", "a1")
                expect(a, "a2", muldiv_model(f3, x & 0xFFFF_FFFF, y & 0xFFFF_FFFF))
        a.li("a0", -12345)
        a.li("a1", 77)
        a.muldiv("mul", "a0", "a0", "a1")
        expect(a, "a0", (-12345 * 77) & 0xFFFF_FFFF)
        a.muldiv("mulhu", "zero", "a0", "a1")
        a.bnez("zero", "bad")

    await expect_ok(dut, program(body), max_cycles=12_000)


@cocotb.test()
async def test_muldiv_cost_and_minstret(dut):
    """mcycle advances by an M instruction's fixed cost, minstret by one, for every operation
    (the counters are read in a CSR instruction's EXECUTE)"""

    def body(a):
        a.li("a0", -7)
        a.li("a1", 3)
        for op in Asm.MUL_OPS:
            a.csrr("t0", "mcycle")
            a.csrr("t1", "minstret")
            a.muldiv(op, "a2", "a0", "a1")
            a.csrr("t2", "mcycle")
            a.csrr("t3", "minstret")
            a.sub("t2", "t2", "t0")
            expect(a, "t2", 2 + 2 + MULDIV_CYCLES)  # the two csrr, then the M instruction
            a.sub("t3", "t3", "t1")
            expect(a, "t3", 3)  # the csrr of minstret, the M instruction, the csrr of mcycle

    await expect_ok(dut, program(body), max_cycles=2_000)


def irq_program():
    """A loop of M instructions with the timer interrupt enabled. The handler checks that
    the interrupted instruction left its rd as it was (a2 = a0 * a0 of the previous pass)
    and then spins at "on_mul" when mepc is the M instruction, else at "on_other"."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("a0", 3)
    a.li("a2", 4)  # as if a pass with a0 = 2 had run
    a.li("t0", 0x80)
    a.csrs("mie", "t0")
    a.li("t0", 0x8)
    a.csrs("mstatus", "t0")
    a.label("loop")
    a.label("on_mul_pc")
    a.muldiv("mul", "a2", "a0", "a0")
    a.addi("a0", "a0", 1)
    a.j("loop")

    a.label("handler")
    a.addi("t1", "a0", -1)
    a.la("t0", "on_mul_pc")
    a.csrr("t2", "mepc")
    a.bne("t2", "t0", "not_mul")
    a.muldiv("mul", "t1", "t1", "t1")  # mepc = the mul: a2 is still the previous pass's
    a.bne("a2", "t1", "bad")
    a.label("on_mul")
    a.j("on_mul")
    a.label("not_mul")
    a.label("on_other")
    a.j("on_other")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels


@cocotb.test()
async def test_interrupt_before_muldiv(dut):
    """An interrupt sampled in an M instruction's FETCH traps with mepc = it and no write
    (the unit isn't started); raised at every point of a loop pass, it lands there at least once"""
    words, labels = irq_program()
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    landed = set()
    for irq_after in range(60, 60 + MULDIV_CYCLES + 6):
        pcs, _ = await run_program(dut, words, irq_after=irq_after, max_cycles=irq_after + 200, start_clock=False)
        assert labels["bad"] not in pcs, f"irq_after {irq_after}: rd changed by a trapped M instruction"
        end = {labels["on_mul"]: "mul", labels["on_other"]: "other"}.get(pcs[-1])
        assert end is not None, f"irq_after {irq_after}: no trap taken; last PC {pcs[-1]:#x}"
        landed.add(end)
    assert landed == {"mul", "other"}, landed
