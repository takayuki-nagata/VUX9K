# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Boot Manager UART CLI verification (test_soc_boot.py).

Boots the real Boot Manager (build/firmware/firmware.hex) on the RTL SoC and drives
every interactive command over the virtual UART, checking the exact response text
the firmware prints (firmware/boot_manager/src/main.rs). The 'w' flashing protocol
and the SD-boot execution chain are covered by test_soc_hardware_flow.py instead.
"""

import os
import re
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import BANNER, mbr_sector, send_and_wait, slot_image, start_soc

PROMPT = b"vux> "
SLOT1_NAME = "CliTest"
# Slot 1 payload: lui a1,0x40000; addi a0,zero,'#'; sb a0,0(a1); j .
SLOT1_PAYLOAD = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)


def mbr_with_marker() -> bytes:
    """Sector 0 with the 0x55AA signature plus a recognizable first row for the 'd' dump."""
    mbr = bytearray(mbr_sector())
    mbr[0:4] = b"\xeb\x3c\x90\x4d"
    return bytes(mbr)


def dump_rows(sector: bytes) -> list[str]:
    """The hex-dump rows dump_sector_0() prints: only the first 256 bytes (16 rows), with an
    8-digit offset and an extra gap after byte 7. The signature verdict still covers 510-511."""
    rows = []
    for row in range(16):
        chunk = sector[row * 16 : (row + 1) * 16]
        cols = "".join(f"{b:02X} " + (" " if i == 7 else "") for i, b in enumerate(chunk))
        rows.append(f"  0x{row * 16:08X}: {cols}")
    return rows


async def cmd(ser, text: str, until: bytes = PROMPT, timeout_cycles: int = 10_000_000) -> str:
    """Send a CLI command and return the response up to (and including) `until`."""
    resp = await send_and_wait(ser, text, until, timeout_cycles=timeout_cycles)
    echo = f"[CMD:0x{ord(text[0]):02X}]"
    assert echo in resp, f"missing command echo {echo} for {text!r}: {resp!r}"
    return resp


def expect_lines(resp: str, lines: list[str], what: str):
    for line in lines:
        assert line in resp, f"{what}: missing {line!r} in {resp!r}"


@cocotb.test()
async def test_boot_manager_cli(dut):
    """Every Boot Manager CLI command answers with its documented output"""
    mbr = mbr_with_marker()
    slot1, meta = slot_image(SLOT1_PAYLOAD, slot=1, mode="riscv", name=SLOT1_NAME)
    ser, _ = await start_soc(dut, mbr=False, sd_sectors={0: mbr, **slot1})

    boot = (await ser.wait_for(PROMPT, timeout_cycles=3_000_000)).decode("utf-8", errors="replace")
    expect_lines(boot, [BANNER, "Available Commands:"], "boot banner")

    # h: help menu
    resp = await cmd(ser, "h")
    expect_lines(
        resp,
        ["Available Commands:", "  [l] List program slots catalog (Slots 0-9)", "  [r] Reboot SoC via Resident Loader"],
        "help",
    )

    # l: slot catalog (slot 1 preloaded, everything else empty)
    resp = await cmd(ser, "l")
    expect_lines(
        resp,
        [
            "--- VUX9 Program Slots Catalog (MBR Gap LBA 64-703) ---",
            "  Slot 0 (LBA 64): [Empty / Using BRAM Fallback]",
            f'  Slot 1 (LBA 128): "{SLOT1_NAME}" [RV32I, {meta["size_bytes"]} B] (Default / Button S2)',
            "  Slot 2 (LBA 192): [Empty]",
            "  Slot 9 (LBA 640): [Empty]",
        ],
        "slot list",
    )

    # s1: inspect a valid header -- every field
    resp = await cmd(ser, "s1")
    expect_lines(
        resp,
        [
            "[SD] Inspecting Slot 1 (Sector 128)...",
            '  Magic: 0x56555839 ("VUX9" Valid Header) [OK]',
            f'  Name:  "{SLOT1_NAME}"',
            "  Mode:  1 (RISC-V 32-bit ISA)",
            f"  Size:  {meta['size_bytes']} bytes",
            "  Flags: 0x00000001",
            f"  Version: {meta['version']}",
            f"  CRC32: 0x{meta['crc32']:08X}",
        ],
        "inspect slot 1",
    )

    # s2: inspect an empty slot -- must not claim a valid header
    resp = await cmd(ser, "s2")
    expect_lines(resp, ["[SD] Inspecting Slot 2 (Sector 192)...", "  [Empty or invalid VUX9 header]"], "inspect slot 2")
    assert "[OK]" not in resp, f"empty slot reported as valid: {resp!r}"

    # d: hex dump of sector 0 (first 256 bytes) plus the signature verdict
    resp = await cmd(ser, "d")
    expect_lines(resp, dump_rows(mbr), "sector 0 dump")
    expect_lines(resp, ["[SD] Sector 0 Read Successful. Signature: 55 AA [PASS]"], "sector 0 dump")

    # t: hardware diagnostics; the timer must count ~180,000 ticks in 10 ms at 18 MHz
    resp = await cmd(ser, "t", timeout_cycles=20_000_000)
    expect_lines(
        resp,
        [
            " 1. GPIO LEDs: swept 0-5",
            " 2. User Button (S2): RELEASED\n",
            " 4. MicroSD SPI Card: Detected & Initialized [PASS]",
            "[DIAG] Diagnostics Complete.",
        ],
        "diagnostics",
    )
    m = re.search(r" 3\. Timer \(mtime\): 10ms = (\d+) ticks, \d+ CPU cycles \[PASS\]", resp)
    assert m, f"diagnostics: no timer line in {resp!r}"
    ticks = int(m.group(1))
    assert 175_000 <= ticks <= 185_000, f"10 ms measured as {ticks} ticks, expected ~180,000"

    # k: Knight Rider LED animation
    resp = await cmd(ser, "k", timeout_cycles=20_000_000)
    expect_lines(resp, ["[LED] Running Knight Rider...", "[LED] Done."], "knight rider")

    # i: SD re-initialization
    resp = await cmd(ser, "i")
    expect_lines(resp, ["[SD] Initializing...", "[SD] Card Ready! [OK]"], "sd init")

    # Unknown commands are echoed and otherwise ignored (no prompt follows)
    await cmd(ser, "z", until=b"[CMD:0x7A]")

    # r: reboot through the Resident Loader back into a working Boot Manager
    resp = await cmd(ser, "r", until=BANNER.encode(), timeout_cycles=20_000_000)
    expect_lines(
        resp, ["[RESET] Rebooting Boot Manager...", "[BOOT] Launching Slot 0 via Resident Loader..."], "reboot"
    )
    resp = (await ser.wait_for(PROMPT, timeout_cycles=5_000_000)).decode("utf-8", errors="replace")
    resp = await cmd(ser, "s1")
    expect_lines(resp, [f'  Name:  "{SLOT1_NAME}"', f"  CRC32: 0x{meta['crc32']:08X}"], "inspect slot 1 after reboot")
    dut._log.info("Boot Manager CLI verified (h/l/s/d/t/k/i/unknown/r)")
