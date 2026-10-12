# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Top-Level SoC Verification Testbench (test_soc_fast.py)
Power-on reset, the first stage booting \\VUX9K\\STAGE2.BIN from a FAT32 card into the
PSRAM, and the CPU running it there (on Icarus, through the real PSRAM controller).
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import BANNER, check_psram_pins, fat_sectors, start_soc, v4_file


@cocotb.test()
async def test_soc_fast_boot(dut):
    """Power-on to the first stage, which loads STAGE2.BIN into the PSRAM and starts it"""
    card = fat_sectors({"VUX9K/STAGE2.BIN": v4_file(version=5)})
    ser, _ = await start_soc(dut, sd_sectors=card)
    dut._log.info("SoC Reset released.")

    # The banner, the key window (100 ms), the card and the file: a few million cycles
    out = await ser.wait_for(b"[BOOT] Starting at 0x80700000", timeout_cycles=6_000_000)
    text = out.decode("utf-8", errors="replace")
    assert BANNER in text, text
    assert '[BOOT] STAGE2.BIN v5 "test" 28 B' in text, text
    # The second stage runs from the PSRAM: '#'
    if b"#" not in out.split(b"Starting at 0x80700000", 1)[1]:
        await ser.wait_for(b"#", timeout_cycles=500_000)
    dut._log.info("STAGE2.BIN loaded into the PSRAM and executed ('#')")
    check_psram_pins(dut)
