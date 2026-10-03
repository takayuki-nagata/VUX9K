# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""rv32i_trap_unit: legality, misalignment and the order in which traps win, against a
model written from the RV32I/Zicsr spec and README's "RV32 CSRs" list."""

import random

import cocotb
from cocotb.triggers import Timer
from unit_models import rand32

MASK = 0xFFFF_FFFF
LUI, AUIPC, JAL, JALR, BRANCH, LOAD, STORE = 0x37, 0x17, 0x6F, 0x67, 0x63, 0x03, 0x23
OP_IMM, OP, FENCE, SYSTEM = 0x13, 0x33, 0x0F, 0x73
OPCODES = (LUI, AUIPC, JAL, JALR, BRANCH, LOAD, STORE, OP_IMM, OP, FENCE, SYSTEM)
ECALL, EBREAK, MRET, WFI = 0x0000_0073, 0x0010_0073, 0x3020_0073, 0x1050_0073

# README "RV32 CSRs": implemented, counters, and the ones that read as 0
CSRS = (0x300, 0x301, 0x304, 0x305, 0x340, 0x341, 0x342, 0x343, 0x344)
CSRS += (0xB00, 0xB02, 0xB80, 0xB82, 0xC00, 0xC01, 0xC02, 0xC80, 0xC81, 0xC82)
CSRS += (0x310, 0xF11, 0xF12, 0xF13, 0xF14, 0xF15, 0x7A0, 0x7A1, 0x7A2, 0x7A3)


def illegal(w):
    op, f3, rs1, f7, csr = w & 0x7F, (w >> 12) & 7, (w >> 15) & 0x1F, w >> 25, w >> 20
    if op in (LUI, AUIPC, JAL):
        return False
    if op == JALR:
        return f3 != 0
    if op == BRANCH:
        return f3 in (2, 3)
    if op == LOAD:
        return f3 not in (0, 1, 2, 4, 5)
    if op == STORE:
        return f3 not in (0, 1, 2)
    if op == OP_IMM:
        return f7 != 0 if f3 == 1 else f7 not in (0, 0x20) if f3 == 5 else False
    if op == OP:
        return not (f7 == 0 or (f7 == 0x20 and f3 in (0, 5)))
    if op == FENCE:
        return f3 not in (0, 1)
    if op == SYSTEM:
        if f3 == 0:
            return w not in (ECALL, EBREAK, MRET, WFI)
        writes = f3 in (1, 5) or rs1 != 0
        return f3 == 4 or csr not in CSRS or (csr >> 10 == 3 and writes)
    return True


def model(s):
    """(trap_entry, trap_cause, trap_val, trap_return) for the input state s."""
    if not s["exec"]:
        return 0, 0, 0, 0
    w, imm = s["instr"], s["imm"]
    op, f3 = w & 0x7F, (w >> 12) & 7
    if s["irq"]:
        return 1, s["irq_cause"], 0, 0
    if illegal(w):
        return 1, 2, w, 0
    fetch_mis = {JAL: imm & 2, BRANCH: imm & 2 and s["branch_take"], JALR: s["addr_low"] & 2}.get(op, 0)
    if fetch_mis:
        return 1, 0, s["jalr_target"] if op == JALR else (s["pc"] + imm) & MASK, 0
    if w == ECALL:
        return 1, 11, 0, 0
    if w == EBREAK:
        return 1, 3, s["pc"], 0
    if op in (LOAD, STORE) and ((f3 & 3 == 1 and s["addr_low"] & 1) or (f3 & 3 == 2 and s["addr_low"])):
        return 1, 4 if op == LOAD else 6, s["mem_addr"], 0
    return 0, 0, 0, int(w == MRET)


def random_instr():
    r = random.random()
    if r < 0.1:
        return random.choice((ECALL, EBREAK, MRET, WFI))
    if r < 0.25:  # a CSR access: listed or not, reading or writing
        csr = random.choice(CSRS) if random.random() < 0.6 else random.getrandbits(12)
        rs1 = 0 if random.random() < 0.4 else random.getrandbits(5)
        return csr << 20 | rs1 << 15 | random.randrange(8) << 12 | random.getrandbits(5) << 7 | SYSTEM
    if r < 0.3:
        return random.getrandbits(32)
    op = random.choice(OPCODES)
    f7 = random.choice((0, 0x20, random.getrandbits(7)))
    return f7 << 25 | random.getrandbits(18) << 7 | op


def random_state():
    w = random_instr()
    return {
        "exec": random.random() < 0.9,
        "irq": random.random() < 0.1,
        "irq_cause": 0x8000_0000 | random.choice((3, 7, 11)),
        "instr": w,
        "imm": rand32(),
        "pc": rand32() & ~3,
        "branch_take": random.random() < 0.5,
        "addr_low": random.randrange(4),
        "jalr_target": rand32() & ~1,
        "mem_addr": rand32(),
    }


async def check(dut, s):
    for name, value in s.items():
        getattr(dut, name).value = int(value)
    await Timer(1, unit="ns")
    got = tuple(int(getattr(dut, n).value) for n in ("trap_entry", "trap_cause", "trap_val", "trap_return"))
    want = tuple(int(v) for v in model(s))
    assert got == want, f"(entry, cause, val, return) {got} != {want}: " + ", ".join(
        f"{k}=0x{int(v):x}" for k, v in s.items()
    )


@cocotb.test()
async def test_csr_legality(dut):
    """Every CSR address with each access kind: illegal unless listed, and writing a
    read-only one (addr[11:10] == 3) is illegal while reading it isn't"""
    s = random_state() | {"exec": True, "irq": False}
    for csr in range(0x1000):
        for f3, rs1 in ((1, 0), (2, 0), (2, 5), (3, 0), (5, 0), (6, 0), (7, 3)):
            await check(dut, s | {"instr": csr << 20 | rs1 << 15 | f3 << 12 | 1 << 7 | SYSTEM})


@cocotb.test()
async def test_misalignment_matrix(dut):
    """Loads and stores of each width at each offset, and JAL/JALR/branch targets with
    bit 1 set or clear (a taken branch only)"""
    s = random_state() | {"exec": True, "irq": False}
    for op, f3s in ((LOAD, (0, 1, 2, 4, 5)), (STORE, (0, 1, 2))):
        for f3 in f3s:
            for low in range(4):
                await check(dut, s | {"instr": f3 << 12 | op, "addr_low": low})
    for op in (JAL, JALR, BRANCH):
        for imm in (0, 2, 4, 0xFFFF_FFFE):
            for low in range(4):
                for take in (0, 1):
                    await check(dut, s | {"instr": op, "imm": imm, "addr_low": low, "branch_take": take})


@cocotb.test()
async def test_random(dut):
    """Random instructions and states: the right trap (or none) wins"""
    for _ in range(5000):
        await check(dut, random_state())
