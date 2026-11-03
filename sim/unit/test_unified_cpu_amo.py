# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu RV32A as a program sees it: what LR.W, SC.W and an AMO cost against the
instructions they share their path with (README: LR as LW, a successful SC as SW, a failed
SC as an ALU instruction, an AMO two cycles more than LW), SC.W's result and its store.

The lockstep (`amo`) and rv32ua cover the semantics on the SoC; these exist for
`make mutation MCY_TOP=unified_cpu`, which runs only the CPU-level tests. Each program
measures with mcycle, checks itself and spins at "ok" (or "bad", which is mtvec too).
"""

import cocotb
from rv32_asm import Asm
from test_unified_cpu_traps import expect_ok

LR, SC, AMOADD = 0b00010, 0b00011, 0b00000
ADDR = 0x400
SC_DATA = (0x5555_5555, 0xAAAA_AAAA)  # every bit of the store both ways


def timed(a, reg, emit):
    """reg <= the cycles emit() takes, plus the csrr's constant overhead"""
    a.csrr("s0", "mcycle")
    emit()
    a.csrr(reg, "mcycle")
    a.sub(reg, reg, "s0")


def amo_program():
    a = Asm()
    a.la("t0", "bad")
    a.csrw("mtvec", "t0")
    a.li("t1", ADDR)
    a.li("t4", 0x1234_5678)
    a.li("a0", SC_DATA[0])
    a.li("a1", SC_DATA[1])
    timed(a, "s1", lambda: a.lw("t2", 0, "t1"))
    timed(a, "s2", lambda: a.amo(LR, "t2", "zero", "t1"))  # reserves ADDR
    a.bne("s1", "s2", "bad")
    timed(a, "s3", lambda: a.sw("t4", 0, "t1"))  # a plain store keeps the reservation
    timed(a, "s4", lambda: a.amo(SC, "t3", "a0", "t1"))
    a.bne("s3", "s4", "bad")
    a.bnez("t3", "bad")  # succeeded
    a.amo(LR, "t2", "zero", "t1")
    a.amo(SC, "t3", "a1", "t1")
    a.bnez("t3", "bad")
    timed(a, "s5", lambda: a.addi("t2", "t2", 1))
    timed(a, "s6", lambda: a.amo(SC, "t3", "t4", "t1"))  # the last SC cleared the reservation
    a.bne("s5", "s6", "bad")
    a.li("t6", 1)
    a.bne("t3", "t6", "bad")  # failed
    timed(a, "s7", lambda: a.amo(AMOADD, "t2", "t4", "t1"))
    a.addi("s1", "s1", 2)
    a.bne("s1", "s7", "bad")
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels


@cocotb.test()
async def test_lr_sc_amo_cycles_and_sc_store(dut):
    """LR costs an LW, a successful SC an SW (storing rs2 on all lanes, rd = 0), a failed SC an
    ADDI (no store, rd = 1), an AMO an LW plus two; the failed SC never reaches the bus"""
    stores: list[tuple[int, int, int]] = []
    await expect_ok(dut, amo_program(), stores=stores)
    for value in SC_DATA:
        sc = [s for s in stores if s[1] == value]
        assert sc and all(s == (ADDR, value, 0xF) for s in sc), f"SC's store of {value:#x}: {stores}"
    assert all(s[0] == ADDR for s in stores), f"stores: {stores}"
