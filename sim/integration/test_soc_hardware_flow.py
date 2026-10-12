# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb End-to-End Hardware Test Flow Runner (test_soc_hardware_flow.py)

The one RTL test that drives the two-stage boot's whole UART and SD chain: transfer v2
(fw_common::upload::upload_v2, as tools/vux_tool.py's send_upload_v2 speaks it) carries
the provisional second stage into the PSRAM for a RAM boot; the second stage writes a new
\\VUX9K\\STAGE2.BIN to the FAT32 card (fat::stage2::install); after a soft reset the first
stage boots that file. The first stage's other commands are test_soc_boot.py's.
"""

import binascii
import os
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import S1_PROMPT, S2_PROMPT, fat_sectors, send_and_wait, stage2_file, start_soc, v4_file

TIMEOUT = 4_000_000
BLOCK = 512


async def upload_v2(ser, data: bytes) -> str:
    """Transfer v2 after its command: the length, each block after its [READY-BLK:i], the
    CRC-32; returns the device's lines up to [OK]."""
    out = await send_and_wait(ser, "", b"[READY-V2]\n", timeout_cycles=TIMEOUT, settle_bits=0)
    ser.write(struct.pack("<I", len(data)))
    out += await send_and_wait(ser, "", f"[LEN:{len(data)}]".encode(), timeout_cycles=TIMEOUT, settle_bits=0)
    for i in range(0, len(data), BLOCK):
        out += await send_and_wait(ser, "", f"[READY-BLK:{i // BLOCK}]\n".encode(), TIMEOUT, settle_bits=0)
        ser.write(data[i : i + BLOCK])
    out += await send_and_wait(ser, "", b"[READY-CRC]\n", timeout_cycles=TIMEOUT, settle_bits=0)
    ser.write(struct.pack("<I", binascii.crc32(data) & 0xFFFFFFFF))
    out += await send_and_wait(ser, "", b"[OK]", timeout_cycles=TIMEOUT, settle_bits=0)
    return out


@cocotb.test()
async def test_soc_hardware_flow(dut):
    """RAM-boot the second stage over the UART, install a STAGE2.BIN with it, boot that"""
    # A FAT32 card with \VUX9K\ but no second stage: the first stage stops at its prompt
    ser, _ = await start_soc(dut, sd_sectors=fat_sectors({"VUX9K/README.TXT": b"flow\n"}))
    resp = await send_and_wait(ser, "", S1_PROMPT, timeout_cycles=6_000_000, settle_bits=0)
    assert "[BOOT] STAGE2.BIN: not found; STAGE2.OLD: not found" in resp, resp
    dut._log.info("[PASS] 1: first stage at its prompt (no STAGE2.BIN)")

    # RAM boot: `u`, then the second stage over transfer v2 (16 KB: ~25M cycles at 115200)
    ser.reset_input_buffer()
    ser.write(b"u")
    await upload_v2(ser, stage2_file())
    resp = await send_and_wait(ser, "", S2_PROMPT, timeout_cycles=TIMEOUT, settle_bits=0)
    assert "[BOOT] Starting at 0x80700000" in resp, resp
    assert "[S2] a0=0x00000001 a1=0x00000800 a2=0x00000001" in resp, resp
    dut._log.info("[PASS] 2: second stage RAM-booted, a0-a2 handed over")

    # Install: `n`, then a small STAGE2.BIN that prints '#'
    ser.reset_input_buffer()
    ser.write(b"n")
    await upload_v2(ser, v4_file(version=7))
    resp = await send_and_wait(ser, "", S2_PROMPT, timeout_cycles=20_000_000, settle_bits=0)
    assert "[INSTALL] OK" in resp, resp
    dut._log.info("[PASS] 3: STAGE2.BIN installed on the card")

    # Soft reset: the first stage boots the installed file
    resp = await send_and_wait(ser, "r", "[BOOT] Starting at 0x80700000".encode(), timeout_cycles=10_000_000)
    assert '[BOOT] STAGE2.BIN v7 "test" 28 B' in resp, resp
    await ser.wait_for(b"#", timeout_cycles=500_000)
    dut._log.info("[PASS] 4: soft reset, the first stage booted the installed STAGE2.BIN ('#')")
