# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.triggers import Timer
from unit_models import sext


@cocotb.test()
async def test_rv32i_decoder(dut):
    """Test RISC-V RV32I instruction decoder and immediate generation"""

    # 1. R-type: ADD x3, x1, x2 (0x002081B3)
    dut.instruction.value = 0x002081B3
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x33
    assert int(dut.rd.value) == 3
    assert int(dut.funct3.value) == 0
    assert int(dut.rs1.value) == 1
    assert int(dut.rs2.value) == 2
    assert int(dut.funct7.value) == 0

    # 2. I-type: ADDI x1, x2, -5 (0xFFB10093)
    dut.instruction.value = 0xFFB10093
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x13
    assert int(dut.rd.value) == 1
    assert int(dut.funct3.value) == 0
    assert int(dut.rs1.value) == 2
    assert int(dut.imm.value) == 0xFFFF_FFFB  # -5 sign-extended

    # 3. S-type: SW x3, 8(x2) (0x00312423)
    dut.instruction.value = 0x00312423
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x23
    assert int(dut.funct3.value) == 2
    assert int(dut.rs1.value) == 2
    assert int(dut.rs2.value) == 3
    assert int(dut.imm.value) == 8

    # 4. B-type: BEQ x1, x2, 16 (0x00208863)
    dut.instruction.value = 0x00208863
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x63
    assert int(dut.funct3.value) == 0
    assert int(dut.rs1.value) == 1
    assert int(dut.rs2.value) == 2
    assert int(dut.imm.value) == 16

    # 5. U-type: LUI x5, 0x12345 (0x123452B7)
    dut.instruction.value = 0x123452B7
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x37
    assert int(dut.rd.value) == 5
    assert int(dut.imm.value) == 0x1234_5000

    # 6. J-type: JAL x1, 24 (0x018000EF)
    dut.instruction.value = 0x018000EF
    await Timer(1, unit="ns")
    assert int(dut.opcode.value) == 0x6F
    assert int(dut.rd.value) == 1
    assert int(dut.imm.value) == 24

    dut._log.info("Instruction decoder & immediate generation verified successfully [PASS]")


OPCODES = {
    "R_TYPE": 0x33,
    "I_TYPE": 0x13,
    "LOAD": 0x03,
    "STORE": 0x23,
    "BRANCH": 0x63,
    "JAL": 0x6F,
    "JALR": 0x67,
    "LUI": 0x37,
    "AUIPC": 0x17,
    "SYSTEM": 0x73,
    "FENCE": 0x0F,
}


def bits(x, hi, lo):
    return (x >> lo) & ((1 << (hi - lo + 1)) - 1)


def imm_model(w):
    """The immediate rv32i_decode produces for instruction word w (RISC-V spec formats)."""
    op = w & 0x7F
    if op in (0x13, 0x03, 0x67):  # I
        return sext(bits(w, 31, 20), 12)
    if op == 0x23:  # S
        return sext(bits(w, 31, 25) << 5 | bits(w, 11, 7), 12)
    if op == 0x63:  # B
        return sext(bits(w, 31, 31) << 12 | bits(w, 7, 7) << 11 | bits(w, 30, 25) << 5 | bits(w, 11, 8) << 1, 13)
    if op in (0x37, 0x17):  # U
        return w & 0xFFFF_F000
    if op == 0x6F:  # J
        return sext(bits(w, 31, 31) << 20 | bits(w, 19, 12) << 12 | bits(w, 20, 20) << 11 | bits(w, 30, 21) << 1, 21)
    if op == 0x73:  # SYSTEM: the CSR zimm
        return bits(w, 19, 15)
    return 0  # R-type, FENCE and unknown opcodes


async def check(dut, w):
    dut.instruction.value = w
    await Timer(1, unit="ns")
    want = {
        "opcode": bits(w, 6, 0),
        "rd": bits(w, 11, 7),
        "funct3": bits(w, 14, 12),
        "rs1": bits(w, 19, 15),
        "rs2": bits(w, 24, 20),
        "funct7": bits(w, 31, 25),
        "imm": imm_model(w),
        "csr_addr": bits(w, 31, 20),
        "uimm": bits(w, 19, 15),
    }
    for name, value in want.items():
        got = int(getattr(dut, name).value)
        assert got == value, f"instruction 0x{w:08x}: {name} = 0x{got:x}, want 0x{value:x}"


@cocotb.test()
async def test_decoder_immediate_edges(dut):
    """Each format with the immediate's bits all clear, all set, and each one alone"""
    for op in OPCODES.values():
        for high in (0, 0xFFFF_FF80) + tuple(1 << b for b in range(7, 32)):
            await check(dut, high | op)


@cocotb.test()
async def test_decoder_random(dut):
    """Random instruction words: every opcode the CPU knows, and random (unknown) ones"""
    ops = list(OPCODES.values())
    for _ in range(3000):
        op = random.choice(ops) if random.random() < 0.85 else random.getrandbits(7)
        await check(dut, (random.getrandbits(25) << 7) | op)
