# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def test_hack_translator(dut):
    """Test Hack-to-RV32I micro-op instruction translator"""

    # 1. A-Instruction: @1234 (0x04D2)
    dut.instr_16.value = 0x04D2
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 0
    assert int(dut.imm.value) == 1234
    assert int(dut.we_reg_a.value) == 1
    assert int(dut.we_reg_d.value) == 0
    assert int(dut.we_mem.value) == 0
    assert int(dut.use_imm.value) == 1
    assert int(dut.jump_cond.value) == 0

    # 2. C-Instruction: D=A (0xEC10 -> 111 0 110000 010 000)
    dut.instr_16.value = 0xEC10
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.we_reg_d.value) == 1
    assert int(dut.we_reg_a.value) == 0
    assert int(dut.we_mem.value) == 0
    assert int(dut.use_mem.value) == 0
    assert int(dut.use_imm.value) == 0

    # 3. C-Instruction: D=D+1 (0xE7D0 -> 111 0 011111 010 000)
    dut.instr_16.value = 0xE7D0
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.we_reg_d.value) == 1
    assert int(dut.use_imm.value) == 1
    assert int(dut.imm.value) == 1

    # 4. C-Instruction: M=D (0xE308 -> 111 0 001100 001 000)
    dut.instr_16.value = 0xE308
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.we_mem.value) == 1
    assert int(dut.we_reg_a.value) == 0
    assert int(dut.we_reg_d.value) == 0

    # 5. C-Instruction: D=M (0xFC10 -> 111 1 110000 010 000)
    dut.instr_16.value = 0xFC10
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.use_mem.value) == 1
    assert int(dut.we_reg_d.value) == 1

    # 6. Jump condition: JLT (0xE304 -> 111 0 001100 000 100)
    dut.instr_16.value = 0xE304
    await Timer(1, unit="ns")
    assert int(dut.jump_cond.value) == 0b100

    # 7. C-Instruction: D=A-D (0xE1D0 -> 111 0 000111 010 000)
    dut.instr_16.value = 0xE1D0
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.rs1.value) == 2  # D (constant)
    assert int(dut.rs2.value) == 1  # A (constant)
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 1  # Y (A)
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 7b. C-Instruction: D=M-D (0xF1D0 -> 111 1 000111 010 000)
    dut.instr_16.value = 0xF1D0
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.use_mem.value) == 1
    assert int(dut.op_a_sel.value) == 1  # Y (M)
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 8. C-Instruction: D=-A (0xECD0 -> 111 0 110011 010 000)
    dut.instr_16.value = 0xECD0
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.op_a_sel.value) == 2  # ZERO
    assert int(dut.op_b_sel.value) == 0  # Y (A)
    assert int(dut.we_reg_d.value) == 1

    # 9. C-Instruction: D=-D (0xE3D0 -> 111 0 001111 010 000)
    dut.instr_16.value = 0xE3D0
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 1  # SUB
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 2  # ZERO
    assert int(dut.op_b_sel.value) == 1  # D
    assert int(dut.we_reg_d.value) == 1

    # 10. C-Instruction: D=!A (0xEC50 -> 111 0 110001 010 000)
    dut.instr_16.value = 0xEC50
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 5  # XOR
    assert int(dut.use_imm.value) == 1
    assert int(dut.imm.value) == 0xFFFFFFFF
    assert int(dut.op_a_sel.value) == 1  # Y (A)
    assert int(dut.op_b_sel.value) == 2  # IMM (-1)
    assert int(dut.we_reg_d.value) == 1

    # 11. C-Instruction: D=!D (0xE350 -> 111 0 001101 010 000)
    dut.instr_16.value = 0xE350
    await Timer(1, unit="ns")
    assert int(dut.is_c_instr.value) == 1
    assert int(dut.alu_op.value) == 5  # XOR
    assert int(dut.use_imm.value) == 1
    assert int(dut.imm.value) == 0xFFFFFFFF
    assert int(dut.use_mem.value) == 0
    assert int(dut.op_a_sel.value) == 0  # D
    assert int(dut.op_b_sel.value) == 2  # IMM (-1)
    assert int(dut.we_reg_d.value) == 1

    dut._log.info("Hack micro-op translator verified successfully [PASS]")
