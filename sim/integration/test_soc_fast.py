# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Top-Level SoC Verification Testbench (test_soc_fast.py)
Verifies power-on reset, CPU boot, instruction execution, and initial peripheral activation.
"""

import os
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import start_soc, wait_cycles

VUX_MAGIC = 0x56555839


@cocotb.test()
async def test_soc_fast_boot(dut):
    """Verify top-level SoC power-on reset, SD card loader, boot prompt, and S2 button launch"""
    # Preload Slot 1 (LBA 128) with test payload that writes '#' (0x23) to UART (0x4000_0000):
    # lui a1, 0x40000    -> 0x400005b7
    # addi a0, zero, 0x23 -> 0x02300513
    # sb a0, 0(a1)        -> 0x00a58023
    # j .                 -> 0x0000006f
    payload = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)
    header = struct.pack(
        "<IHHIIIIII32s", VUX_MAGIC, 3, 1, 1, len(payload), 0, 0, 0, 1, b"TestApp\x00".ljust(32, b"\x00")
    )
    # Sector 0 gets the 0x55AA MBR signature from start_soc()
    ser, _ = await start_soc(dut, sd_sectors={128: header + payload})
    dut._log.info("SoC Reset released.")

    # Wait for UART boot banner and prompt 'vux> ' (raises TimeoutError with the output so far)
    await ser.wait_for(b"vux> ", timeout_cycles=3000000)
    dut._log.info("SoC Fast Boot & UART Prompt verified successfully!")

    # -------------------------------------------------------------------------
    # TEST: S2 Button Launch & Full Slot 1 Execution
    # -------------------------------------------------------------------------
    dut._log.info("Testing S2 button launch for Slot 1...")
    dut.btn.value = 0  # Button S2 pressed
    await wait_cycles(27000 * 25)  # 25ms debounce
    dut.btn.value = 1  # Release button
    await wait_cycles(27000 * 15)

    # Wait for Resident Loader "[RL] Slot 1" confirmation
    rl_buf = await ser.wait_for(b"[RL] Slot 1", timeout_cycles=2000000)
    dut._log.info("Resident Loader triggered for Slot 1!")

    # Wait for the loaded application to execute and output '#' (it may already
    # have arrived in the same chunk as the Resident Loader message)
    if b"#" not in rl_buf.split(b"[RL] Slot 1", 1)[1]:
        await ser.wait_for(b"#", timeout_cycles=2000000)
    dut._log.info("Slot 1 loaded and executed payload ('#') successfully!")
