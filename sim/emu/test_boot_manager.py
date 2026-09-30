# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Boot Manager CLI on the emulator: every command's output, as
sim/integration/test_soc_boot.py checks it on the RTL, plus the S2 launch and the
'w' command's timeouts.
"""

import re

from bm_env import BANNER, BOOT_CYCLES, HASH_PAYLOAD, PROMPT, boot, cmd, expect_lines
from vux9k import mbr_sector, ms, press_button, slot_image, wait_for

SLOT1_NAME = "CliTest"


def mbr_with_marker() -> bytes:
    mbr = bytearray(mbr_sector())
    mbr[0:4] = b"\xeb\x3c\x90\x4d"
    return bytes(mbr)


def dump_rows(sector: bytes) -> list:
    rows = []
    for row in range(16):
        chunk = sector[row * 16 : (row + 1) * 16]
        cols = "".join(f"{b:02X} " + (" " if i == 7 else "") for i, b in enumerate(chunk))
        rows.append(f"  0x{row * 16:08X}: {cols}")
    return rows


def test_every_cli_command():
    mbr = mbr_with_marker()
    slot1, meta = slot_image(HASH_PAYLOAD, slot=1, mode="riscv", name=SLOT1_NAME)
    soc, out = boot(mbr=False, sd_sectors={0: mbr, **slot1})
    expect_lines(out, [BANNER, "Available Commands:"], "boot banner")

    expect_lines(cmd(soc, "h"), ["Available Commands:", "  [r] Reboot SoC via Resident Loader"], "help")
    expect_lines(
        cmd(soc, "l"),
        [
            "--- VUX9 Program Slots Catalog (MBR Gap LBA 64-703) ---",
            "  Slot 0 (LBA 64): [Empty / Using BRAM Fallback]",
            f'  Slot 1 (LBA 128): "{SLOT1_NAME}" [RV32I, {meta["size_bytes"]} B] (Default / Button S2)',
            "  Slot 9 (LBA 640): [Empty]",
        ],
        "slot list",
    )
    expect_lines(
        cmd(soc, "s1"),
        [
            '  Magic: 0x56555839 ("VUX9" Valid Header) [OK]',
            f'  Name:  "{SLOT1_NAME}"',
            "  Mode:  1 (RISC-V 32-bit ISA)",
            f"  Size:  {meta['size_bytes']} bytes",
            f"  CRC32: 0x{meta['crc32']:08X}",
        ],
        "inspect slot 1",
    )
    resp = cmd(soc, "s2")
    expect_lines(resp, ["  [Empty or invalid VUX9 header]"], "inspect slot 2")
    assert "[OK]" not in resp

    resp = cmd(soc, "d")
    expect_lines(resp, dump_rows(mbr) + ["[SD] Sector 0 Read Successful. Signature: 55 AA [PASS]"], "dump")

    resp = cmd(soc, "t")
    expect_lines(
        resp,
        [" 1. GPIO LEDs: [PASS]", " 2. User Button (S2): RELEASED [PASS]", "[DIAG] Diagnostics Complete."],
        "diagnostics",
    )
    m = re.search(r" 3\. Timer \(mtime\): 10ms = (\d+) ticks", resp)
    assert m and 175_000 <= int(m.group(1)) <= 185_000, resp

    expect_lines(cmd(soc, "k"), ["[LED] Running Knight Rider...", "[LED] Done."], "knight rider")
    expect_lines(cmd(soc, "i"), ["[SD] Initializing...", "[SD] Card Ready! [OK]"], "sd init")
    cmd(soc, "z", until=b"[CMD:0x7A]")  # unknown: echoed, nothing else

    resp = cmd(soc, "r", until=BANNER.encode())
    expect_lines(resp, ["[RESET] Rebooting Boot Manager...", "[BOOT] Launching Slot 0 via Resident Loader..."], "r")
    mark = len(soc.uart_received())
    wait_for(soc, PROMPT, BOOT_CYCLES, start=mark)
    expect_lines(cmd(soc, "s1"), [f"  CRC32: 0x{meta['crc32']:08X}"], "inspect after reboot")


def test_button_launches_slot_1():
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv", name="S2")
    soc, _ = boot(sd_sectors=slot1)
    mark = len(soc.uart_received())
    press_button(soc)
    out = wait_for(soc, b"#", 3_000_000, start=mark)
    expect_lines(out, ["[BUTTON] S2 Pressed! Launching Slot 1", "[RL] Slot 1"], "S2 launch")
    assert soc.leds is not None


def test_no_sd_card():
    soc, out = boot(card=False)
    resp = cmd(soc, "t")
    assert " 4. MicroSD SPI Card:" in resp and "[PASS]" not in resp.split(" 4. MicroSD SPI Card:")[1].split("\n")[0]


def test_write_command_times_out_without_a_slot_id():
    soc, _ = boot()
    resp = cmd(soc, "w", until=b"[READY]")
    mark = len(soc.uart_received())
    out = wait_for(soc, PROMPT, timeout_cycles=ms(5500), start=mark)
    assert "[SD-ERR] Slot ID timeout!" in resp + out
