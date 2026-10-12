# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The first stage's UART prompt on the RTL SoC (test_soc_boot.py).

Boots the first stage (build/firmware/firmware.hex), keeps it at its prompt with a key
right after the banner, and drives its commands over the virtual UART, checking the text
firmware/stage1/src/main.rs prints; `r` soft-resets it, and with no key it boots
\\VUX9K\\STAGE2.BIN. The RAM boot and installing a second stage are
test_soc_hardware_flow.py's.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import BANNER, S1_PROMPT, fat_sectors, send_and_wait, start_soc, v4_file


async def cmd(ser, text: str, until: bytes = S1_PROMPT, timeout_cycles: int = 10_000_000) -> str:
    """Send a command and return the response up to (and including) `until`."""
    resp = await send_and_wait(ser, text, until, timeout_cycles=timeout_cycles)
    echo = f"[CMD:0x{ord(text[0]):02X}]"
    assert echo in resp, f"missing command echo {echo} for {text!r}: {resp!r}"
    return resp


def expect_lines(resp: str, lines: list[str], what: str):
    for line in lines:
        assert line in resp, f"{what}: missing {line!r} in {resp!r}"


@cocotb.test()
async def test_stage1_prompt(dut):
    """A key keeps the first stage at its prompt; every command answers; r boots again"""
    ser, _ = await start_soc(dut, sd_sectors=fat_sectors({"VUX9K/STAGE2.BIN": v4_file(version=5)}))

    boot = (await ser.wait_for(b"3 MHz\n", timeout_cycles=2_000_000)).decode("utf-8", errors="replace")
    expect_lines(boot, [BANNER], "banner")
    resp = await send_and_wait(ser, "\r", S1_PROMPT, timeout_cycles=3_000_000, settle_bits=0)
    expect_lines(resp, ["[SAFE MODE] Key received: staying at the prompt.", "Commands:"], "safe mode")
    assert "[BOOT]" not in resp, resp

    resp = await cmd(ser, "h")
    expect_lines(resp, ["  [b] Boot \\VUX9K\\STAGE2.BIN (STAGE2.OLD if it is broken)", "  [r] Soft reset"], "help")

    resp = await cmd(ser, "i")
    expect_lines(
        resp,
        [
            "[SD] SDHC card",
            "[FAT] FAT32 at LBA 2048, 512 B clusters",
            '[FAT] \\VUX9K\\STAGE2.BIN: v5 "test" 28 B [OK]',
            "[FAT] \\VUX9K\\STAGE2.OLD: not found",
        ],
        "sd info",
    )

    resp = await cmd(ser, "z")
    expect_lines(resp, ["Unknown command"], "unknown")

    # r: soft reset; no key this time, so the first stage boots STAGE2.BIN ('#')
    resp = await cmd(ser, "r", until=b"[BOOT] Starting at 0x80700000", timeout_cycles=10_000_000)
    expect_lines(resp, ["[RESET] Soft reset", BANNER, '[BOOT] STAGE2.BIN v5 "test" 28 B'], "soft reset")
    await ser.wait_for(b"#", timeout_cycles=500_000)
    dut._log.info("First stage prompt verified (key, h/i/unknown/r, boot after the soft reset)")
