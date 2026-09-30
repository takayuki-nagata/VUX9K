# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Boot-mode handoff from the Resident Loader (test_soc_boot_mode.py).

Its own module because a SoC test that launches a slot leaves the payload in I-RAM,
so the next test in the same simulator process would boot that instead of the
Boot Manager.
"""

import os
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
import hack_asm
from soc_env import slot_image, start_soc, wait_cycles


@cocotb.test()
async def test_soc_boot_hack_slot_starting_with_at0(dut):
    """The Resident Loader passes the slot header's ISA to the CPU (GPIO boot-mode register),
    so a Hack program whose first instruction is `@0` runs: the first-instruction
    heuristic can't tell it from RV32 (README, "ISA selection")."""
    a = hack_asm.Asm()
    a.at(0)  # first instruction 0x0000
    a.c("D=A")
    a.at(ord("H"))
    a.c("D=D+A")
    a.at(0x6000)  # UART data
    a.c("M=D")
    a.label("halt")
    a.at("halt")
    a.c("0;JMP")
    # A .hack binary: big-endian 16-bit instructions (vux_tool packs them for I-RAM)
    payload = b"".join(struct.pack(">H", i) for i in a.instructions())
    slot1, _ = slot_image(payload, slot=1, mode="hack", name="HackAt0")
    ser, _ = await start_soc(dut, sd_sectors=slot1)
    await ser.wait_for(b"vux> ", timeout_cycles=3000000)

    dut.btn.value = 0  # S2 launches Slot 1
    await wait_cycles(18000 * 25)
    dut.btn.value = 1
    out = await ser.wait_for(b"[RL] Slot 1", timeout_cycles=2000000)
    if b"H" not in out.split(b"[RL] Slot 1", 1)[1]:
        await ser.wait_for(b"H", timeout_cycles=2000000)
    assert int(dut.soc.active_mode.value) == 0, "Slot 1 must run in Hack mode"
