# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import FallingEdge, Timer, ClockCycles
from cocotb.clock import Clock

@cocotb.test()
async def test_unified_cpu_hack_and_riscv(dut):
    """Test standalone unified_cpu dual-ISA execution (Hack 16-bit + RISC-V 32-bit) in Multi-cycle FSM"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # =========================================================================
    # TEST 1: Hack 16-bit Mode Execution
    # =========================================================================
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.timer_irq_in.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0
    dut.instr_in.value = (0x000F << 16) | 0x000F # Hack @15 (Load 15 into A/x1)

    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Cycle 1: Fetch -> Cycle 2: Execute (@15) -> Next PC becomes 2
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 0, "Hack mode auto-detection failed!"
    assert int(dut.pc_out.value) == 2, f"Expected PC=2, got {int(dut.pc_out.value)}"

    # PC=2: Hack C-instruction D=A (0xEC10 -> "1110110000010000")
    dut.instr_in.value = (0xEC10 << 16) | 0xEC10
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: Hack C-instruction D=D+1 (0xE7D0 -> "1110011111010000")
    dut.instr_in.value = (0xE7D0 << 16) | 0xE7D0
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 6, f"Expected PC=6, got {int(dut.pc_out.value)}"

    # PC=6: Hack C-instruction M=D (0xE308 -> "1110001100001000")
    dut.instr_in.value = (0xE308 << 16) | 0xE308
    await ClockCycles(dut.clk, 1) # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "Hack memory write assertion failed"
    assert int(dut.data_addr.value) == 15, f"Expected addr=15, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 16, f"Expected data=16, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)

    dut._log.info("[PASS] Unified CPU: Hack 16-bit program execution verified!")

    # =========================================================================
    # TEST 2: RISC-V 32-bit Mode Execution
    # =========================================================================
    dut.rst.value = 0
    dut.instr_in.value = 0x02a00513 # ADDI x10, x0, 42
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Cycle 1: Fetch -> Cycle 2: Execute -> PC becomes 4
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 1, "RISC-V mode auto-detection failed!"
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: ADDI x11, x10, 8 (42 + 8 = 50)
    dut.instr_in.value = 0x00850593
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 8, f"Expected PC=8, got {int(dut.pc_out.value)}"

    # PC=8: SW x11, 100(x0) (Store 50 to address 100)
    dut.instr_in.value = 0x06b02223
    await ClockCycles(dut.clk, 1) # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "SW memory write assertion failed"
    assert int(dut.data_addr.value) == 100, f"Expected addr=100, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 50, f"Expected data=50, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)

    dut._log.info("[PASS] Unified CPU: RISC-V 32-bit program execution verified!")
