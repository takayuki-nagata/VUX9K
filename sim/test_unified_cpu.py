import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_unified_cpu_hack_and_riscv(dut):
    """Test standalone unified_cpu dual-ISA execution (Hack 16-bit + RISC-V 32-bit)"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # =========================================================================
    # TEST 1: Hack 16-bit Mode Execution
    # =========================================================================
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.instr_in.value = 0x000F # Hack @15 (Load 15 into A/x1)

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 0, "Hack mode auto-detection failed!"
    assert int(dut.pc_out.value) == 0

    # PC=0: @15 executes on rising edge -> PC becomes 2
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 2, f"Expected PC=2, got {int(dut.pc_out.value)}"

    # PC=2: Hack C-instruction D=A (0xEC10 -> "1110110000010000")
    dut.instr_in.value = 0xEC10
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: Hack C-instruction D=D+1 (0xE7D0 -> "1110011111010000")
    dut.instr_in.value = 0xE7D0
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 6, f"Expected PC=6, got {int(dut.pc_out.value)}"

    # PC=6: Hack C-instruction M=D (0xE308 -> "1110001100001000")
    dut.instr_in.value = 0xE308
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "Hack memory write assertion failed"
    assert int(dut.data_addr.value) == 15, f"Expected addr=15, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 16, f"Expected data=16, got {int(dut.data_out.value)}"
    await FallingEdge(dut.clk)

    dut._log.info("[PASS] Unified CPU: Hack 16-bit program execution verified!")

    # =========================================================================
    # TEST 2: RISC-V 32-bit Mode Execution
    # =========================================================================
    dut.rst.value = 0
    dut.instr_in.value = 0x02a00513 # ADDI x10, x0, 42
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 1, "RISC-V mode auto-detection failed!"
    assert int(dut.pc_out.value) == 0

    # PC=0: ADDI x10, x0, 42 -> PC becomes 4
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: ADDI x11, x0, 100 (0x06400593)
    dut.instr_in.value = 0x06400593
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 8, f"Expected PC=8, got {int(dut.pc_out.value)}"

    # PC=8: ADD x12, x10, x11 (0x00b50633) -> x12 = 142
    dut.instr_in.value = 0x00b50633
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 12, f"Expected PC=12, got {int(dut.pc_out.value)}"

    # PC=12: SW x12, 0(x10) (0x00c52023) -> Store 142 to addr 42
    dut.instr_in.value = 0x00c52023
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "RISC-V memory write assertion failed"
    assert int(dut.data_addr.value) == 42, f"Expected addr=42, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 142, f"Expected data=142, got {int(dut.data_out.value)}"
    await FallingEdge(dut.clk)

    # PC=16: JAL x0, 8 (0x0080006f) -> Jump PC to 24
    dut.instr_in.value = 0x0080006f
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 24, f"Expected PC=24, got {int(dut.pc_out.value)}"

    dut._log.info("[PASS] Unified CPU: RISC-V 32-bit program execution verified!")
