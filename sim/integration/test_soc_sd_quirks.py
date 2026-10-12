# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SD card edge cases the forgiving default SpiSdCardModel hides (test_soc_sd_quirks.py).

Runs the first stage against a strict SD model (addresses interpreted exactly as the card
type requires, protocol violations recorded) to reproduce the SD-related issues found on
hardware (docs/agents/firmware.md / the IS_SDHC investigation):
- the SPI init preamble must satisfy the SD spec's >= 74-clock power-up
  requirement (it does: SdCard::init() sends 16 bytes of 0xFF = 128 clocks --
  an earlier analysis that counted "16 clocks" was wrong);
- SDSC (byte-addressed) cards across a soft reset, which the second stage uses to return
  to the first: the card type is detected again, at <= 400 kHz although the divider
  (which survives the soft reset) was left at full speed.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import S1_PROMPT, STAGE2_BASE, fat_sectors, hash_program, send_and_wait, start_soc, v4_file


def addressing_violations(sd_model):
    return [v for v in sd_model.violations if not v.startswith("power-up")]


@cocotb.test()
async def test_power_up_preamble(dut):
    """The first SD command is preceded by >= 74 SCLK cycles with CS high (SD spec)"""
    ser, sd_model = await start_soc(dut, sd_card={"sdhc": True, "strict": True})
    await ser.wait_for(S1_PROMPT, timeout_cycles=6_000_000)
    dut._log.info(f"power-up preamble: {sd_model.idle_clocks} SCLK cycles with CS high")
    assert not sd_model.violations, "; ".join(sd_model.violations)


@cocotb.test()
async def test_sdsc_card_across_soft_reset(dut):
    """An SDSC (byte-addressed) card boots STAGE2.BIN, again after the soft reset it does"""
    card = fat_sectors({"VUX9K/STAGE2.BIN": v4_file(hash_program(STAGE2_BASE, soft_reset=True))})
    ser, sd_model = await start_soc(dut, sd_sectors=card, sd_card={"sdhc": False, "strict": True})
    for boot in range(2):
        resp = await send_and_wait(ser, "", "#".encode(), timeout_cycles=8_000_000, settle_bits=0)
        assert '[BOOT] STAGE2.BIN v1 "test"' in resp, f"boot {boot}: {resp!r}"
    assert not addressing_violations(sd_model), "; ".join(addressing_violations(sd_model))
