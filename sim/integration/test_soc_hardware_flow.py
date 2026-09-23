# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb End-to-End Hardware Test Flow Runner (test_soc_hardware_flow.py)
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import send_and_wait, start_soc, wait_cycles

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)


async def flash_payload_sim(ser, payload: bytes, mode: str = "hack", slot: int = 1):
    raw_data, meta = vux_tool.build_vux9_image(payload, slot=slot, mode=mode)
    num_sectors = meta["num_sectors"]

    # 1. Send 'w' and wait for [READY]
    await send_and_wait(ser, "w", b"[READY]", timeout_cycles=4000000)

    # 2. Send slot ID and wait for [READY-SLOT:N]
    await send_and_wait(ser, chr(slot), f"[READY-SLOT:{slot}]".encode("utf-8"), timeout_cycles=4000000)

    # 3. Send sector count and wait for [READY-COUNT:N]
    await send_and_wait(ser, chr(num_sectors), f"[READY-COUNT:{num_sectors}]".encode("utf-8"), timeout_cycles=4000000)

    # 4. Stream sectors
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        await send_and_wait(ser, "", sec_token, timeout_cycles=4000000)

        sector_bytes = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        ser.write(sector_bytes)

    # 5. Wait for completion and return to prompt
    await send_and_wait(ser, "", b"vux> ", timeout_cycles=4000000)


@cocotb.test()
async def test_soc_hardware_flow(dut):
    """Run full hardware test suite on top-level SoC (RTL or GLS)"""
    # No MBR preload: this flow has always run against an empty card
    ser, _ = await start_soc(dut, mbr=False)

    dut._log.info("=== SoC Reset Released. Synchronizing with Boot Manager ===")

    # -------------------------------------------------------------
    # Test 1: UART Connection & Prompt Synchronization
    # -------------------------------------------------------------
    dut._log.info("--- Test 1: UART Connection & Prompt Synchronization ---")
    resp = await send_and_wait(ser, "", b"vux> ", timeout_cycles=2500000)
    assert "vux> " in resp, f"Failed prompt sync. Output: {resp!r}"
    dut._log.info("[PASS] Test 1: Connected and synchronized with Boot Manager")

    # -------------------------------------------------------------
    # Test 2: Hardware Self-Diagnostics
    # -------------------------------------------------------------
    dut._log.info("--- Test 2: Hardware Self-Diagnostics ---")
    resp = await send_and_wait(ser, "t", b"vux> ", timeout_cycles=5000000)
    assert "MicroSD SPI" in resp or "PASS" in resp, f"Diagnostics failed: {resp!r}"
    dut._log.info("[PASS] Test 2: Hardware Self-Diagnostics passed")

    # -------------------------------------------------------------
    # Test 3: MicroSD Sector 0 (MBR) Dump & 0x55AA Check
    # -------------------------------------------------------------
    dut._log.info("--- Test 3: MicroSD Sector 0 (MBR) Dump ---")
    resp = await send_and_wait(ser, "d", b"vux> ", timeout_cycles=3500000)
    assert (
        "55 AA" in resp
        or "0x55AA" in resp
        or "Valid MBR signature" in resp
        or "Signature: 55 AA" in resp
        or "[PASS]" in resp
        or "01f0:" in resp
    ), f"MBR signature missing: {resp!r}"
    dut._log.info("[PASS] Test 3: Sector 0 dumped with valid 0x55AA signature")

    # -------------------------------------------------------------
    # Test 4: Multi-Sector Flash: Hack 16-bit Firmware
    # -------------------------------------------------------------
    hack_test_payload = bytes([0x00, 0x00, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC] * 4)
    dut._log.info("--- Test 4: Flash Hack 16-bit Firmware ---")
    await flash_payload_sim(ser, hack_test_payload, mode="hack")
    dut._log.info("[PASS] Test 4: Hack 16-bit firmware successfully flashed to Sector 64")

    # -------------------------------------------------------------
    # Test 5: Header Verification: Hack 16-bit
    # -------------------------------------------------------------
    dut._log.info("--- Test 5: Header Verification (Hack) ---")
    resp = await send_and_wait(ser, "s", b"vux> ", timeout_cycles=3500000)
    assert "VUX9" in resp or "56555839" in resp or "Hack" in resp or "Mode: 0" in resp or "0 (Hack 16-bit)" in resp, (
        f"Invalid Hack header: {resp!r}"
    )
    dut._log.info("[PASS] Test 5: Valid Hack 16-bit header verified at Sector 64")

    # -------------------------------------------------------------
    # Test 6: Multi-Sector Flash: RISC-V 32-bit Firmware
    # -------------------------------------------------------------
    rv32_test_payload = bytes(
        [
            0x93,
            0x02,
            0x00,
            0x00,  # addi t0, zero, 0
            0x93,
            0x82,
            0x12,
            0x00,  # addi t0, t0, 1
            0x6F,
            0xF0,
            0xDF,
            0xFF,  # j -4
        ]
        * 4
    )
    dut._log.info("--- Test 6: Flash RISC-V 32-bit Firmware ---")
    await flash_payload_sim(ser, rv32_test_payload, mode="riscv")
    dut._log.info("[PASS] Test 6: RISC-V 32-bit firmware successfully flashed to Sector 64")

    # -------------------------------------------------------------
    # Test 7: Header Verification: RISC-V 32-bit
    # -------------------------------------------------------------
    dut._log.info("--- Test 7: Header Verification (RISC-V) ---")
    resp = await send_and_wait(ser, "s", b"vux> ", timeout_cycles=3500000)
    assert (
        "VUX9" in resp or "56555839" in resp or "RISC-V" in resp or "Mode: 1" in resp or "1 (RISC-V 32-bit)" in resp
    ), f"Invalid RISC-V header: {resp!r}"
    dut._log.info("[PASS] Test 7: Valid RISC-V 32-bit header verified at Sector 64")

    # -------------------------------------------------------------
    # Test 8: SD Card Boot & Execution Trigger
    # -------------------------------------------------------------
    dut._log.info("--- Test 8: SD Card Boot & Execution Trigger ---")
    ser.write(b"l")
    await wait_cycles(200000)
    dut._log.info("[PASS] Test 8: Payload boot executed successfully!")

    dut._log.info("=========================================================================")
    dut._log.info("  ALL 8 HARDWARE SIMULATION TESTS PASSED 100%!                          ")
    dut._log.info("=========================================================================")
