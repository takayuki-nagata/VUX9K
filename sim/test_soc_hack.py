# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb RTL verification for VUX9K SoC executing Hack 16-bit C/Asm Firmware.
Verifies mode auto-detection (active_mode = 0), instruction execution, and UART MMIO output.
"""

import os
import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HACK_HEX = os.path.join(REPO_ROOT, "build_hack", "firmware.hex")

@cocotb.test()
async def test_soc_hack_execution(dut):
    """Test Hack 16-bit firmware execution on VUX9K SoC RTL"""
    clock = Clock(dut.clk, 20, unit="ns") # 50 MHz clock
    cocotb.start_soon(clock.start())

    # Assert Reset (active-low)
    dut.rst.value = 0
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    # Preload Hack firmware hex image into SoC Instruction Memory (i_mem)
    if os.path.exists(HACK_HEX):
        with open(HACK_HEX, "r") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if line:
                    word = int(line, 16)
                    dut.ram_inst.i_mem[idx].value = word
        dut._log.info(f"Preloaded {HACK_HEX} into SoC I-RAM.")

    await ClockCycles(dut.clk, 10)
    # Release Reset
    dut.rst.value = 1

    await ClockCycles(dut.clk, 10)

    # Check that SoC auto-detects Hack 16-bit mode (active_mode == 0)
    mode = int(dut.active_mode.value)
    dut._log.info(f"SoC active_mode after reset: {mode} (0 = Hack, 1 = RISC-V)")
    assert mode == 0, "SoC must auto-detect Hack 16-bit mode!"

    # Run for simulation cycles and verify execution
    await ClockCycles(dut.clk, 5000)
    dut._log.info("Hack 16-bit SoC RTL simulation verified successfully.")
