# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""next_pc_unit: branch conditions, jump targets and the priority of MEM_WAIT, trap
entry and MRET, in RV32 and Hack mode, against a model."""

import random

import cocotb
import fcov
from cocotb.triggers import Timer
from unit_models import EDGES32, rand32, signed32

MASK = 0xFFFF_FFFF
BRANCH, JAL, JALR, OP_IMM, LOAD = 0x63, 0x6F, 0x67, 0x13, 0x03
BEQ, BNE, BLT, BGE, BLTU, BGEU = 0, 1, 4, 5, 6, 7


def branch_model(f3, a, b):
    lt, ltu = signed32(a) < signed32(b), a < b
    return {BEQ: a == b, BNE: a != b, BLT: lt, BGE: not lt, BLTU: ltu, BGEU: not ltu}.get(f3, False)


def hack_take(jump, comp):
    c = comp & 0xFFFF
    zero, neg = c == 0, bool(c & 0x8000)
    return [False, not zero and not neg, zero, not neg, neg, not zero, neg or zero, True][jump]


def rv_next(s):
    if s["mem_wait"]:
        return s["pc_plus_4"]
    if s["trap_entry"]:
        return s["mtvec"]
    if s["trap_return"]:
        return s["mepc"]
    if s["op"] == BRANCH:
        return (s["pc"] + s["imm"]) & MASK if branch_model(s["funct3"], s["rs1"], s["rs2"]) else s["pc_plus_4"]
    if s["op"] == JAL:
        return (s["pc"] + s["imm"]) & MASK
    if s["op"] == JALR:
        return s["mem_addr"] & ~1 & MASK
    return s["pc_plus_4"]


def hack_next(s):
    if not s["mem_wait"] and hack_take(s["hack_jump"], s["hack_comp"]):
        return (s["hack_a"] << 1) & MASK
    return (s["pc"] + 2) & MASK


async def apply(dut, **s):
    for name, value in s.items():
        getattr(dut, name).value = int(value)
    await Timer(1, unit="ns")


def random_state(riscv):
    pc = rand32() & ~1
    return {
        "riscv_mode": riscv,
        "mem_wait": random.random() < 0.15,
        "pc": pc,
        "pc_plus_4": (pc + 4) & MASK,
        "op": random.choice((BRANCH, BRANCH, JAL, JALR, OP_IMM, LOAD)),
        "funct3": random.randrange(8),
        "imm": rand32(),
        "rs1": rand32(),
        "rs2": rand32(),
        "mem_addr": rand32(),
        "trap_entry": random.random() < 0.15,
        "trap_return": random.random() < 0.15,
        "mtvec": rand32(),
        "mepc": rand32(),
        "hack_jump": random.randrange(8),
        "hack_comp": rand32(),
        "hack_a": rand32(),
    }


def _source(s):
    """What selects the next PC in RV32 mode (rv_next's priority)."""
    if not s["riscv_mode"]:
        return None
    for key in ("mem_wait", "trap_entry", "trap_return"):
        if s[key]:
            return key
    return {BRANCH: "branch", JAL: "jal", JALR: "jalr"}.get(s["op"], "next")


def _branch(s):
    """(funct3, taken, operand signs differ) of an RV32 branch that decides the next PC."""
    if _source(s) != "branch" or s["funct3"] not in (BEQ, BNE, BLT, BGE, BLTU, BGEU):
        return None
    differ = int((s["rs1"] ^ s["rs2"]) >> 31 & 1)
    return (s["funct3"], int(branch_model(s["funct3"], s["rs1"], s["rs2"])), differ)


# Every priority level, and every branch condition taken and not taken with operands of
# equal and of different signs (equal operands have equal signs: no BEQ taken / BNE not
# taken with different ones)
@fcov.point("npc.source", ("mem_wait", "trap_entry", "trap_return", "branch", "jal", "jalr", "next"), xf=_source)
@fcov.point(
    "npc.branch",
    [
        (f3, t, d)
        for f3 in (BEQ, BNE, BLT, BGE, BLTU, BGEU)
        for t in (0, 1)
        for d in (0, 1)
        if (f3, t, d) not in ((BEQ, 1, 1), (BNE, 0, 1))
    ],
    xf=_branch,
)
def sample(s):
    pass


async def check(dut, s):
    sample(s)
    await apply(dut, **s)
    ctx = ", ".join(f"{k}=0x{int(v):x}" for k, v in s.items())
    if s["riscv_mode"]:
        assert int(dut.branch_take.value) == branch_model(s["funct3"], s["rs1"], s["rs2"]), f"branch_take: {ctx}"
        assert int(dut.jalr_target.value) == s["mem_addr"] & ~1 & MASK, f"jalr_target: {ctx}"
    want = rv_next(s) if s["riscv_mode"] else hack_next(s)
    assert int(dut.next_pc.value) == want, f"next_pc 0x{int(dut.next_pc.value):08x} != 0x{want:08x}: {ctx}"


@cocotb.test()
async def test_branch_conditions_on_edges(dut):
    """Every branch funct3 on every pair of boundary operands (incl. the signs that the
    synthesized BLT/BGE once got wrong, AGENTS.md)"""
    s = random_state(True) | {"mem_wait": False, "trap_entry": False, "trap_return": False, "op": BRANCH}
    for f3 in range(8):
        for a in EDGES32:
            for b in EDGES32:
                await check(dut, s | {"funct3": f3, "rs1": a, "rs2": b, "imm": 0xFFFF_FFF8})
    fcov.export()


@cocotb.test()
async def test_priority(dut):
    """MEM_WAIT beats trap entry beats MRET beats the instruction's own target"""
    base = random_state(True) | {"op": JAL, "imm": 0x100, "pc": 0x1000, "pc_plus_4": 0x1004}
    base |= {"mtvec": 0x2000, "mepc": 0x3000}
    for mem_wait in (0, 1):
        for trap_entry in (0, 1):
            for trap_return in (0, 1):
                await check(dut, base | {"mem_wait": mem_wait, "trap_entry": trap_entry, "trap_return": trap_return})
    fcov.export()


@cocotb.test()
async def test_hack_jumps(dut):
    """Every Hack jump condition on comp values around 0 and the 16-bit sign; only comp[15:0]
    counts"""
    s = random_state(False) | {"mem_wait": False, "pc": 0x10, "hack_a": 0x4321}
    for jump in range(8):
        for comp in (0, 1, 0x7FFF, 0x8000, 0xFFFF, 0x1_0000, 0xFFFF_0000, 0x1_8000):
            await check(dut, s | {"hack_jump": jump, "hack_comp": comp})
    await check(dut, s | {"hack_jump": 7, "mem_wait": True})  # MEM_WAIT: no jump yet


@cocotb.test()
async def test_random(dut):
    for _ in range(3000):
        await check(dut, random_state(random.random() < 0.75))
    fcov.export()
