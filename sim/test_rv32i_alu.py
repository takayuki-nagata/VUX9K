# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import Timer

OP_ADD = 0
OP_SUB = 1
OP_SLL = 2
OP_SLT = 3
OP_SLTU = 4
OP_XOR = 5
OP_SRL = 6
OP_SRA = 7
OP_OR = 8
OP_AND = 9
OP_COPY_B = 10
OP_COPY_A = 11

@cocotb.test()
async def test_alu_operations(dut):
    """Test all ALU arithmetic, logic, and comparison operations"""

    # 1. ADD: 15 + 25 = 40
    dut.a.value = 15
    dut.b.value = 25
    dut.alu_op.value = OP_ADD
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 40
    assert int(dut.zero.value) == 0

    # 2. SUB: 25 - 15 = 10
    dut.a.value = 25
    dut.b.value = 15
    dut.alu_op.value = OP_SUB
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 10
    assert int(dut.zero.value) == 0

    # 3. SUB with zero result: 25 - 25 = 0 (zero flag = 1)
    dut.a.value = 25
    dut.b.value = 25
    dut.alu_op.value = OP_SUB
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0
    assert int(dut.zero.value) == 1

    # 4. SLL: 1 << 4 = 16
    dut.a.value = 1
    dut.b.value = 4
    dut.alu_op.value = OP_SLL
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 16

    # 5. SLT: signed (-5 < 5) -> 1
    dut.a.value = 0xFFFF_FFFB # -5 in two's complement
    dut.b.value = 5
    dut.alu_op.value = OP_SLT
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 1

    # 6. SLTU: unsigned (0xFFFF_FFFB < 5) -> 0
    dut.a.value = 0xFFFF_FFFB
    dut.b.value = 5
    dut.alu_op.value = OP_SLTU
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0

    # 7. XOR: 0x0F0F_0F0F ^ 0xFFFF_0000 = 0xF0F0_0F0F
    dut.a.value = 0x0F0F_0F0F
    dut.b.value = 0xFFFF_0000
    dut.alu_op.value = OP_XOR
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0xF0F0_0F0F

    # 8. SRL: 0x8000_0000 >> 4 = 0x0800_0000
    dut.a.value = 0x8000_0000
    dut.b.value = 4
    dut.alu_op.value = OP_SRL
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0x0800_0000

    # 9. SRA: (int32)0x8000_0000 >> 4 = 0xF800_0000
    dut.a.value = 0x8000_0000
    dut.b.value = 4
    dut.alu_op.value = OP_SRA
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0xF800_0000

    # 10. OR: 0x1234_0000 | 0x0000_5678 = 0x1234_5678
    dut.a.value = 0x1234_0000
    dut.b.value = 0x0000_5678
    dut.alu_op.value = OP_OR
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0x1234_5678

    # 11. AND: 0x1234_5678 & 0x0000_FFFF = 0x0000_5678
    dut.a.value = 0x1234_5678
    dut.b.value = 0x0000_FFFF
    dut.alu_op.value = OP_AND
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0x0000_5678

    # 12. COPY_B: b = 0xDEAD_BEEF
    dut.a.value = 0x1111_1111
    dut.b.value = 0xDEAD_BEEF
    dut.alu_op.value = OP_COPY_B
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0xDEAD_BEEF

    # 13. COPY_A: a = 0xCAFE_BABE
    dut.a.value = 0xCAFE_BABE
    dut.b.value = 0x2222_2222
    dut.alu_op.value = OP_COPY_A
    await Timer(1, unit="ns")
    assert int(dut.result.value) == 0xCAFE_BABE

    dut._log.info("All ALU operations verified successfully [PASS]")
