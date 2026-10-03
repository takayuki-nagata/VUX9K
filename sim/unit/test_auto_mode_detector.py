# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


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


RV_OPCODES = (0x33, 0x13, 0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73)


def detect(word):
    """README's rule for the first instruction: 1 = RV32, 0 = Hack, None = undecided"""
    if word == 0:
        return None
    if word & 3 == 3 and word & 0x7F in RV_OPCODES:
        return 1
    if word >> 13 & 7 == 7 or (not word & 0x8000 and word & 0x7FFF):
        return 0
    return None


def random_word():
    r = random.random()
    if r < 0.2:
        return random.choice(
            (0, 0x0000_0001, 0x0000_7FFF, 0x0000_8000, 0x0000_E000, 0x0000_0013, 0x0000_000F, 0xEC10_0000, 0xFFFF_FFFF)
        )
    if r < 0.45:  # an RV32 word (or a FENCE, which isn't on the list)
        return random.getrandbits(25) << 7 | random.choice(RV_OPCODES + (0x0F,))
    if r < 0.7:  # a Hack C- or A-instruction in the low half, random upper half
        low = 0xE000 | random.getrandbits(13) if random.random() < 0.5 else random.getrandbits(15)
        return random.getrandbits(16) << 16 | low
    return random.getrandbits(32)


@cocotb.test()
async def test_random_against_model(dut):
    """Random first words, soft resets with and without a boot mode, and resets, against
    a model of the latch: the ISA is decided once and kept until the next (soft) reset"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.soft_rst.value = 0
    dut.boot_mode_valid.value = 0
    dut.boot_mode.value = 0
    dut.first_instr.value = 0
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    latched, mode = 0, 1
    for _ in range(3000):
        word = random_word()
        soft = random.random() < 0.08
        valid, boot = random.getrandbits(1), random.getrandbits(1)
        dut.first_instr.value, dut.soft_rst.value = word, int(soft)
        dut.boot_mode_valid.value, dut.boot_mode.value = valid, boot
        await FallingEdge(dut.clk)
        if soft:
            latched, mode = (1, boot) if valid else (0, 1)
        elif not latched and detect(word) is not None:
            latched, mode = 1, detect(word)
        ctx = f"word=0x{word:08x} soft={int(soft)} valid={valid} boot={boot}"
        assert int(dut.is_riscv_mode.value) == mode, ctx
        if random.random() < 0.01:  # a power-on reset now and then
            dut.rst.value = 0
            await FallingEdge(dut.clk)
            dut.rst.value = 1
            latched, mode = 0, 1
            assert int(dut.is_riscv_mode.value) == 1, "power-on reset starts in RV32"
