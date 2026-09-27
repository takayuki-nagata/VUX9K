# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles


@cocotb.test()
async def test_auto_mode_detection(dut):
    """Test first-instruction RISC-V vs Hack ISA mode auto-detection"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # Case 1: RISC-V first instruction (ADDI x0, x0, 0 = 0x00000013)
    dut.soft_rst.value = 0
    dut.boot_mode_valid.value = 0
    dut.boot_mode.value = 0
    dut.rst.value = 0
    dut.first_instr.value = 0x00000013
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)

    assert int(dut.is_riscv_mode.value) == 1, "Should detect RISC-V 32-bit mode!"

    # Subsequent instruction change should not alter latched mode
    dut.first_instr.value = 0x00000000  # Hack instruction
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

    # Case 3: Soft Reset unlatches mode back to RISC-V detection
    dut.soft_rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.soft_rst.value = 0
    dut.first_instr.value = 0x00000013
    await ClockCycles(dut.clk, 2)
    assert int(dut.is_riscv_mode.value) == 1, "Soft reset must allow re-detecting RISC-V mode!"

    dut._log.info("Auto mode detector verified successfully [PASS]")


@cocotb.test()
async def test_auto_mode_explicit_on_soft_reset(dut):
    """A valid boot mode during soft reset latches that ISA, whatever the first instruction"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.soft_rst.value = 0
    dut.boot_mode_valid.value = 0
    dut.boot_mode.value = 0
    dut.first_instr.value = 0x00000013
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    assert int(dut.is_riscv_mode.value) == 1

    # Soft reset with an explicit Hack mode; the first word (@0, D=A) matches neither rule
    dut.first_instr.value = 0xEC100000
    dut.boot_mode_valid.value = 1
    dut.boot_mode.value = 0
    dut.soft_rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.soft_rst.value = 0
    dut.boot_mode_valid.value = 0
    await ClockCycles(dut.clk, 4)
    assert int(dut.is_riscv_mode.value) == 0, "explicit Hack mode must be latched"

    # Soft reset without a valid mode falls back to detection (RV32 until detected)
    dut.first_instr.value = 0x00000013
    dut.soft_rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.soft_rst.value = 0
    await ClockCycles(dut.clk, 2)
    assert int(dut.is_riscv_mode.value) == 1, "without a valid mode, detection applies"
