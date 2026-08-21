# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import Timer

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
    assert int(dut.imm.value) == 0xFFFF_FFFB # -5 sign-extended

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
