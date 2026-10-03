# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.triggers import Timer
from unit_models import HACK_COMP, alu_model


@cocotb.test()
async def test_hack_translator(dut):
    """Test Hack-to-RV32I micro-op instruction translator"""

    # 1. A-Instruction: @1234 (0x04D2)
    dut.instr_16.value = 0x04D2
    await Timer(1, unit="ns")
    assert int(dut.imm.value) == 1234
    assert int(dut.we_reg_a.value) == 1
    assert int(dut.we_reg_d.value) == 0
    assert int(dut.we_mem.value) == 0
    assert int(dut.jump_cond.value) == 0

    # 2. C-Instruction: D=A (0xEC10 -> 111 0 110000 010 000)
    dut.instr_16.value = 0xEC10
    await Timer(1, unit="ns")
    assert int(dut.we_reg_d.value) == 1
    assert int(dut.we_reg_a.value) == 0
    assert int(dut.we_mem.value) == 0
    assert int(dut.use_mem.value) == 0

    # 3. C-Instruction: D=D+1 (0xE7D0 -> 111 0 011111 010 000)
    dut.instr_16.value = 0xE7D0
    await Timer(1, unit="ns")
    assert int(dut.we_reg_d.value) == 1
    assert int(dut.imm.value) == 1

    # 4. C-Instruction: M=D (0xE308 -> 111 0 001100 001 000)
    dut.instr_16.value = 0xE308
    await Timer(1, unit="ns")
    assert int(dut.we_mem.value) == 1
    assert int(dut.we_reg_a.value) == 0
    assert int(dut.we_reg_d.value) == 0

    # 5. C-Instruction: D=M (0xFC10 -> 111 1 110000 010 000)
    dut.instr_16.value = 0xFC10
    await Timer(1, unit="ns")
    assert int(dut.use_mem.value) == 1
    assert int(dut.we_reg_d.value) == 1

    # 6. Jump condition: JLT (0xE304 -> 111 0 001100 000 100)
    dut.instr_16.value = 0xE304
    await Timer(1, unit="ns")
    assert int(dut.jump_cond.value) == 0b100

    # 7. C-Instruction: D=A-D (0xE1D0 -> 111 0 000111 010 000)
    dut.instr_16.value = 0xE1D0
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 1  # Y (A)
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 7b. C-Instruction: D=M-D (0xF1D0 -> 111 1 000111 010 000)
    dut.instr_16.value = 0xF1D0
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.use_mem.value) == 1
    assert int(dut.op_a_sel.value) == 1  # Y (M)
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 8. C-Instruction: D=-A (0xECD0 -> 111 0 110011 010 000)
    dut.instr_16.value = 0xECD0
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.op_a_sel.value) == 2  # ZERO
    assert int(dut.op_b_sel.value) == 0  # Y (A)
    assert int(dut.we_reg_d.value) == 1

    # 9. C-Instruction: D=-D (0xE3D0 -> 111 0 001111 010 000)
    dut.instr_16.value = 0xE3D0
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 2  # ZERO
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 10. C-Instruction: D=!A (0xEC50 -> 111 0 110001 010 000)
    dut.instr_16.value = 0xEC50
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 5  # XOR
    assert int(dut.imm.value) == 0xFFFFFFFF
    assert int(dut.op_a_sel.value) == 1  # Y (A)
    assert int(dut.op_b_sel.value) == 2  # IMM (-1)
    assert int(dut.we_reg_d.value) == 1

    # 11. C-Instruction: D=!D (0xE350 -> 111 0 001101 010 000)
    dut.instr_16.value = 0xE350
    await Timer(1, unit="ns")
    assert int(dut.alu_op.value) == 5  # XOR
    assert int(dut.imm.value) == 0xFFFFFFFF
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 0  # D
    assert int(dut.op_b_sel.value) == 2  # IMM (-1)
    assert int(dut.we_reg_d.value) == 1

    dut._log.info("Hack micro-op translator verified successfully [PASS]")


USES_Y = {c for c, f in HACK_COMP.items() if f(0, 0) != f(0, 0x1234)}


def execute(dut, d, a, m):
    """What unified_cpu computes from the translator's outputs: the ALU on the selected
    operands, Y being M when use_mem is set (16-bit result)."""
    y = m if int(dut.use_mem.value) else a
    imm = int(dut.imm.value)
    op_a = {0: d, 1: y, 2: 0}[int(dut.op_a_sel.value)]
    op_b = {0: y, 1: d, 2: imm}[int(dut.op_b_sel.value)]
    return alu_model(int(dut.alu_op.value), op_a, op_b) & 0xFFFF


@cocotb.test()
async def test_every_c_instruction_computes_its_comp(dut):
    """Every C-instruction with a defined comp (each a bit, dest, jump and the two unused
    bits): run through an ALU model with random D/A/M, the micro-op gives the comp
    table's value; dest/jump pass through; M is read exactly when the comp uses it"""
    for comp, f in HACK_COMP.items():
        for a_bit in (0, 1):
            for xx in range(4):
                for dest in range(8):
                    for jump in range(8):
                        instr = 1 << 15 | xx << 13 | a_bit << 12 | comp << 6 | dest << 3 | jump
                        dut.instr_16.value = instr
                        await Timer(1, unit="ns")
                        d, a, m = (random.getrandbits(16) for _ in range(3))
                        want = f(d, m if a_bit else a) & 0xFFFF
                        ctx = f"instr 0x{instr:04X} D=0x{d:04X} A=0x{a:04X} M=0x{m:04X}"
                        assert execute(dut, d, a, m) == want, f"comp, {ctx}"
                        assert int(dut.we_reg_a.value) == dest >> 2, ctx
                        assert int(dut.we_reg_d.value) == dest >> 1 & 1, ctx
                        assert int(dut.we_mem.value) == dest & 1, ctx
                        assert int(dut.jump_cond.value) == jump, ctx
                        if comp in USES_Y:
                            assert int(dut.use_mem.value) == a_bit, f"use_mem, {ctx}"


@cocotb.test()
async def test_a_instructions(dut):
    """@value: A <- the 15-bit value through the ALU, nothing else written, no jump"""
    for value in [0, 1, 0x3FFF, 0x4000, 0x7FFF] + [random.getrandbits(15) for _ in range(500)]:
        dut.instr_16.value = value
        await Timer(1, unit="ns")
        assert execute(dut, random.getrandbits(16), random.getrandbits(16), random.getrandbits(16)) == value
        assert (int(dut.we_reg_a.value), int(dut.we_reg_d.value), int(dut.we_mem.value)) == (1, 0, 0)
        assert int(dut.jump_cond.value) == 0 and int(dut.use_mem.value) == 0
