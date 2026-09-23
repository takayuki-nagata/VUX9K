# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SD card edge cases the forgiving default SpiSdCardModel hides (test_soc_sd_quirks.py).

Runs the real Boot Manager against a strict SD model (addresses interpreted exactly
as the card type requires, protocol violations recorded) to reproduce the SD-related
issues found on hardware (AGENTS.md / the IS_SDHC investigation):
- the SPI init preamble must satisfy the SD spec's >= 74-clock power-up
  requirement (it does: SdCard::init() sends 16 bytes of 0xFF = 128 clocks --
  an earlier analysis that counted "16 clocks" was wrong);
- SDSC (byte-addressed) cards across a Resident Loader reboot, where IS_SDHC is
  re-detected from scratch and the mailbox encodes `slot | sdhc_bit`.
"""

import os
import struct
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import send_and_wait, slot_image, start_soc

PROMPT = b"vux> "
NAME = "SdQuirk"
# lui a1,0x40000; addi a0,zero,'#'; sb a0,0(a1); j .
PAYLOAD = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)


def addressing_violations(sd_model):
    return [v for v in sd_model.violations if not v.startswith("power-up")]


@cocotb.test()
async def test_power_up_preamble(dut):
    """The first SD command is preceded by >= 74 SCLK cycles with CS high (SD spec)"""
    ser, sd_model = await start_soc(dut, sd_card={"sdhc": True, "strict": True})
    await ser.wait_for(PROMPT, timeout_cycles=3_000_000)
    dut._log.info(f"power-up preamble: {sd_model.idle_clocks} SCLK cycles with CS high")
    assert not sd_model.violations, "; ".join(sd_model.violations)


@cocotb.test()
async def test_sdsc_card_across_reboot(dut):
    """An SDSC (byte-addressed) card works for list/inspect, a Resident Loader reboot, and slot boot"""
    slot1, meta = slot_image(PAYLOAD, slot=1, mode="riscv", name=NAME)
    ser, sd_model = await start_soc(dut, sd_sectors=slot1, sd_card={"sdhc": False, "strict": True})
    await ser.wait_for(PROMPT, timeout_cycles=3_000_000)

    resp = await send_and_wait(ser, "s1", PROMPT, timeout_cycles=4_000_000)
    assert f'  Name:  "{NAME}"' in resp and f"  CRC32: 0x{meta['crc32']:08X}" in resp, resp

    # Reboot into the Boot Manager through the Resident Loader (mailbox target = 0 | sdhc_bit)
    await send_and_wait(ser, "r", b"VUX9K Dual-ISA", timeout_cycles=20_000_000)
    await ser.wait_for(PROMPT, timeout_cycles=5_000_000)
    resp = await send_and_wait(ser, "s1", PROMPT, timeout_cycles=4_000_000)
    assert f'  Name:  "{NAME}"' in resp, f"slot 1 unreadable after reboot: {resp!r}"

    # Boot the slot through the Resident Loader
    resp = await send_and_wait(ser, "1", b"[RL] Slot 1", timeout_cycles=4_000_000)
    if "#" not in resp.split("[RL] Slot 1", 1)[1]:
        await ser.wait_for(b"#", timeout_cycles=4_000_000)

    assert not addressing_violations(sd_model), "; ".join(addressing_violations(sd_model))
