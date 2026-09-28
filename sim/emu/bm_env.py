# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Shared setup for the firmware tests on the emulator (Boot Manager + Resident Loader)."""

import os
import struct

from vux9k import FIRMWARE_DIR, REPO_ROOT, UART_BIT, start_soc, wait_for

PROMPT = b"vux> "
# Boot Manager start to prompt: SD init (retries without a card) and the slot-0 update
# check read up to a whole slot, ~10-20M cycles; this is a generous upper bound
BOOT_CYCLES = 60_000_000
BANNER = "VUX9K Dual-ISA RISC-V / Hack SoC Boot Manager (v1)"
BOOT_MGR_VERSION = 10  # firmware/boot_manager/src/main.rs
# lui a1,0x40000; addi a0,zero,'#'; sb a0,0(a1); j .
HASH_PAYLOAD = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)
BM_BIN = os.path.join(FIRMWARE_DIR, "firmware.bin")
HACK_BIN = os.path.join(REPO_ROOT, "build", "hack", "firmware.bin")


def boot(**kwargs):
    """Power on and wait for the Boot Manager prompt; returns (soc, boot output)."""
    soc = start_soc(**kwargs)
    return soc, wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)


def cmd(soc, text: str, until: bytes = PROMPT, timeout_cycles: int = 20_000_000) -> str:
    """Send a CLI command (after a short pause, like a person) and return the reply."""
    soc.run(50 * UART_BIT)
    start = len(soc.uart_received())
    soc.uart_send(text.encode())
    resp = wait_for(soc, until, timeout_cycles=timeout_cycles, start=start)
    echo = f"[CMD:0x{ord(text[0]):02X}]"
    assert echo in resp, f"missing command echo {echo} for {text!r}: {resp!r}"
    return resp


def expect_lines(resp: str, lines, what: str):
    for line in lines:
        assert line in resp, f"{what}: missing {line!r} in {resp!r}"
