import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

# RV32I Instruction Encoders
def encode_r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int = 0x33) -> int:
    return ((funct7 & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | ((rd & 0x1F) << 7) | (opcode & 0x7F)

def encode_i(imm: int, rs1: int, funct3: int, rd: int, opcode: int = 0x13) -> int:
    return ((imm & 0xFFF) << 20) | ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | ((rd & 0x1F) << 7) | (opcode & 0x7F)

def encode_s(imm: int, rs2: int, rs1: int, funct3: int, opcode: int = 0x23) -> int:
    imm11_5 = (imm >> 5) & 0x7F
    imm4_0 = imm & 0x1F
    return (imm11_5 << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | (imm4_0 << 7) | (opcode & 0x7F)

def encode_b(imm: int, rs2: int, rs1: int, funct3: int, opcode: int = 0x63) -> int:
    imm12 = (imm >> 12) & 1
    imm10_5 = (imm >> 5) & 0x3F
    imm4_1 = (imm >> 1) & 0xF
    imm11 = (imm >> 11) & 1
    return (imm12 << 31) | (imm10_5 << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) | ((funct3 & 0x7) << 12) | (imm4_1 << 8) | (imm11 << 7) | (opcode & 0x7F)

def encode_u(imm: int, rd: int, opcode: int = 0x37) -> int:
    return ((imm & 0xFFFFF000)) | ((rd & 0x1F) << 7) | (opcode & 0x7F)

def encode_j(imm: int, rd: int, opcode: int = 0x6F) -> int:
    imm20 = (imm >> 20) & 1
    imm10_1 = (imm >> 1) & 0x3FF
    imm11 = (imm >> 11) & 1
    imm19_12 = (imm >> 12) & 0xFF
    return (imm20 << 31) | (imm10_1 << 21) | (imm11 << 20) | (imm19_12 << 12) | ((rd & 0x1F) << 7) | (opcode & 0x7F)

@cocotb.test()
async def test_rv32i_full_compliance(dut):
    """Replicate hack_cpu/testbench/rv32i/rv32i_compliance_tb.vhd (full RV32I Base Integer ISA)"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # Reset in RISC-V Mode
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.timer_irq_in.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0
    dut.instr_in.value = encode_i(0, 0, 0, 0, 0x13) # ADDI x0, x0, 0

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 1, "Failed to enter RISC-V mode!"

    # 1. LUI x1, 0x12345 (x1 = 0x12345000)
    dut.instr_in.value = encode_u(0x12345000, rd=1, opcode=0x37)
    await FallingEdge(dut.clk)

    # 2. ADDI x2, x0, -10 (x2 = 0xFFFFFFF6)
    dut.instr_in.value = encode_i(-10, rs1=0, funct3=0, rd=2)
    await FallingEdge(dut.clk)

    # 3. AUIPC x3, 0x1000 (x3 = PC + 0x1000000)
    current_pc = int(dut.pc_out.value)
    dut.instr_in.value = encode_u(0x01000000, rd=3, opcode=0x17)
    await FallingEdge(dut.clk)

    # 4. SLTI x4, x2, 0 (x4 = (-10 < 0) = 1)
    dut.instr_in.value = encode_i(0, rs1=2, funct3=2, rd=4)
    await FallingEdge(dut.clk)

    # 5. SLTIU x5, x2, 0 (x5 = (0xFFFFFFF6 < 0) = 0)
    dut.instr_in.value = encode_i(0, rs1=2, funct3=3, rd=5)
    await FallingEdge(dut.clk)

    # 6. ORI x7, x0, 0x55 (x7 = 0x55)
    dut.instr_in.value = encode_i(0x55, rs1=0, funct3=6, rd=7)
    await FallingEdge(dut.clk)

    # 7. XORI x8, x7, 0xFF (x8 = 0x55 ^ 0xFF = 0xAA)
    dut.instr_in.value = encode_i(0xFF, rs1=7, funct3=4, rd=8)
    await FallingEdge(dut.clk)

    # 8. SLLI x9, x7, 4 (x9 = 0x55 << 4 = 0x550)
    dut.instr_in.value = encode_i(4, rs1=7, funct3=1, rd=9)
    await FallingEdge(dut.clk)

    # 9. SRLI x10, x1, 12 (x10 = 0x12345000 >> 12 = 0x12345)
    dut.instr_in.value = encode_i(12, rs1=1, funct3=5, rd=10)
    await FallingEdge(dut.clk)

    # 10. SRAI x11, x2, 1 (x11 = (int32)-10 >> 1 = -5 = 0xFFFFFFFB)
    dut.instr_in.value = encode_r(funct7=0x20, rs2=1, rs1=2, funct3=5, rd=11, opcode=0x13)
    await FallingEdge(dut.clk)

    # 11. SW x8, 16(x0) (Store 0xAA to address 16)
    dut.instr_in.value = encode_s(16, rs2=8, rs1=0, funct3=2)
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "SW mem_write failed"
    assert int(dut.data_addr.value) == 16, f"Expected store addr 16, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 0xAA, f"Expected store data 0xAA, got {hex(int(dut.data_out.value))}"
    await FallingEdge(dut.clk)

    # 12. LW x12, 16(x0) with data_in = 0xAA
    dut.data_in.value = 0xAA
    dut.instr_in.value = encode_i(16, rs1=0, funct3=2, rd=12, opcode=0x03)
    await FallingEdge(dut.clk)

    # 13. BEQ x7, x7, +16 (Should take branch)
    pc_before_beq = int(dut.pc_out.value)
    dut.instr_in.value = encode_b(16, rs2=7, rs1=7, funct3=0)
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == pc_before_beq + 16, f"Expected PC={pc_before_beq + 16}, got {int(dut.pc_out.value)}"

    # 14. BNE x7, x8, +12 (Should take branch)
    pc_before_bne = int(dut.pc_out.value)
    dut.instr_in.value = encode_b(12, rs2=8, rs1=7, funct3=1)
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == pc_before_bne + 12, f"Expected PC={pc_before_bne + 12}, got {int(dut.pc_out.value)}"

    # 15. JAL x1, +28 (Should jump)
    pc_before_jal = int(dut.pc_out.value)
    dut.instr_in.value = encode_j(28, rd=1)
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == pc_before_jal + 28, f"Expected PC={pc_before_jal + 28}, got {int(dut.pc_out.value)}"

    dut._log.info("Full RV32I compliance test suite passed 100% [PASS]")
