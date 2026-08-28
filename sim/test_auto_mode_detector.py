# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import ClockCycles, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_auto_mode_detection(dut):
    """Test first-instruction RISC-V vs Hack ISA mode auto-detection"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # Case 1: RISC-V first instruction (ADDI x0, x0, 0 = 0x00000013)
    dut.rst.value = 0
    dut.first_instr.value = 0x00000013
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)

    assert int(dut.is_riscv_mode.value) == 1, "Should detect RISC-V 32-bit mode!"

    # Subsequent instruction change should not alter latched mode
    dut.first_instr.value = 0x00000000 # Hack instruction
    await ClockCycles(dut.clk, 2)
    assert int(dut.is_riscv_mode.value) == 1, "Mode must remain latched as RISC-V!"

    # Case 2: Hack first instruction (A-instruction @15 = 0x0000000F)
    dut.rst.value = 0
    dut.first_instr.value = 0x0000000F
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)

    assert int(dut.is_riscv_mode.value) == 0, "Should detect Hack 16-bit mode!"

    # Subsequent change should stay in Hack mode
    dut.first_instr.value = 0x00000013
    await ClockCycles(dut.clk, 2)
    assert int(dut.is_riscv_mode.value) == 0, "Mode must remain latched as Hack!"

    dut._log.info("Auto mode detector verified successfully [PASS]")
