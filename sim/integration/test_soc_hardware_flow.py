# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb End-to-End Hardware Test Flow Runner (test_soc_hardware_flow.py)

The one test that drives the full UART flashing protocol ('w' -> slot ID -> sector
count -> per-sector streaming) through tools/vux_tool.py's image builder, checks what
actually landed on the SD card, verifies the boot headers via the CLI, and boots the
flashed RISC-V payload through the Resident Loader. The other CLI commands are
covered by test_soc_boot.py.
"""

import os
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import send_and_wait, slot_lba, start_soc, vux_tool

SLOT = 1
PROMPT = b"vux> "
TIMEOUT = 4_000_000


async def flash_payload_sim(ser, sd_model, payload: bytes, *, mode: str, name: str, slot: int = SLOT) -> dict:
    """Flash payload into slot over the 'w' protocol; return vux_tool's image metadata."""
    raw_data, meta = vux_tool.build_vux9_image(payload, slot=slot, name=name, mode=mode)
    num_sectors = meta["num_sectors"]

    # 1. Send 'w' and wait for [READY]
    await send_and_wait(ser, "w", b"[READY]", timeout_cycles=TIMEOUT)

    # 2. Send slot ID and wait for [READY-SLOT:N]
    await send_and_wait(ser, chr(slot), f"[READY-SLOT:{slot}]".encode(), timeout_cycles=TIMEOUT)

    # 3. Send sector count and wait for [READY-COUNT:N]
    await send_and_wait(ser, chr(num_sectors), f"[READY-COUNT:{num_sectors}]".encode(), timeout_cycles=TIMEOUT)

    # 4. Stream sectors
    for sec_idx in range(num_sectors):
        await send_and_wait(ser, "", f"[READY-SEC:{sec_idx}]".encode(), timeout_cycles=TIMEOUT)
        ser.write(raw_data[sec_idx * 512 : (sec_idx + 1) * 512])

    # 5. Completion report, then back to the prompt
    done = f"[SD] Successfully wrote {num_sectors} sectors to Slot {slot} (Sector {slot_lba(slot)})! [OK]"
    resp = await send_and_wait(ser, "", PROMPT, timeout_cycles=TIMEOUT)
    assert done in resp, f"missing {done!r} after streaming: {resp!r}"

    # What reached the card must be exactly the image vux_tool built
    for sec_idx in range(num_sectors):
        got = sd_model.get_sector(slot_lba(slot) + sec_idx)
        want = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        assert got == want, f"SD sector {slot_lba(slot) + sec_idx} differs from the flashed image"
    return meta


async def verify_header(ser, meta: dict, *, mode_line: str, slot: int = SLOT):
    resp = await send_and_wait(ser, f"s{slot}", PROMPT, timeout_cycles=TIMEOUT)
    for line in [
        f"[SD] Inspecting Slot {slot} (Sector {slot_lba(slot)})...",
        '  Magic: 0x56555839 ("VUX9" Valid Header) [OK]',
        f'  Name:  "{meta["name"]}"',
        f"  Mode:  {mode_line}",
        f"  Size:  {meta['size_bytes']} bytes",
        f"  CRC32: 0x{meta['crc32']:08X}",
    ]:
        assert line in resp, f"slot {slot} header: missing {line!r} in {resp!r}"


@cocotb.test()
async def test_soc_hardware_flow(dut):
    """Flash Hack and RISC-V payloads over UART, verify them, and boot the RISC-V one"""
    # Flash onto an empty card
    ser, sd_model = await start_soc(dut, mbr=False)
    dut._log.info("=== SoC Reset Released. Synchronizing with Boot Manager ===")

    # -------------------------------------------------------------
    # Test 1: UART Connection & Prompt Synchronization
    # -------------------------------------------------------------
    await send_and_wait(ser, "", PROMPT, timeout_cycles=2_500_000)
    dut._log.info("[PASS] Test 1: Connected and synchronized with Boot Manager")

    # -------------------------------------------------------------
    # Tests 2-3: Flash and verify a Hack 16-bit image
    # -------------------------------------------------------------
    hack_payload = bytes([0x00, 0x00, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC] * 4)
    meta = await flash_payload_sim(ser, sd_model, hack_payload, mode="hack", name="HackFlow")
    dut._log.info("[PASS] Test 2: Hack image flashed; SD contents match")
    await verify_header(ser, meta, mode_line="0 (Hack 16-bit ISA)")
    dut._log.info("[PASS] Test 3: Hack header verified via 's1'")

    # -------------------------------------------------------------
    # Tests 4-5: Flash and verify a RISC-V image (overwrites the same slot)
    # -------------------------------------------------------------
    # lui a1,0x40000; addi a0,zero,'#'; sb a0,0(a1); j .  -> prints '#' on the UART
    rv32_payload = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)
    meta = await flash_payload_sim(ser, sd_model, rv32_payload, mode="riscv", name="RvFlow")
    dut._log.info("[PASS] Test 4: RISC-V image flashed; SD contents match")
    await verify_header(ser, meta, mode_line="1 (RISC-V 32-bit ISA)")
    dut._log.info("[PASS] Test 5: RISC-V header verified via 's1'")

    # -------------------------------------------------------------
    # Test 6: Boot the flashed slot through the Resident Loader and run it
    # -------------------------------------------------------------
    resp = await send_and_wait(ser, str(SLOT), f"[RL] Slot {SLOT}".encode(), timeout_cycles=TIMEOUT)
    assert f"[BOOT] Launching Slot {SLOT} via Resident Loader..." in resp, f"no boot message: {resp!r}"
    tail = resp.split(f"[RL] Slot {SLOT}", 1)[1]
    if "#" not in tail:
        await ser.wait_for(b"#", timeout_cycles=TIMEOUT)
    dut._log.info("[PASS] Test 6: Flashed RISC-V payload booted and printed '#'")
