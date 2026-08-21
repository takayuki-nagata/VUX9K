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

    dut._log.info("Hack micro-op translator verified successfully [PASS]")
