# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Gate-Level SoC Verification Testbench (test_soc_gls_fast.py)
Boots the synthesized netlist in Safe Mode (S2 held) and checks the Boot Manager reaches its prompt.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import BANNER, start_soc

SAFE_MODE = "[SAFE MODE] Button S2 held. Bypassing Slot 0 auto-update."


@cocotb.test()
async def test_soc_gls_fast_boot(dut):
    """Verify synthesized gate-level SoC netlist boot and initial UART transmission"""
    # Hold Button S2 through reset for Boot Manager Safe Mode.
    #
    # `main()` unconditionally calls SdCard::ensure_init() before the Safe Mode
    # check (see firmware/src/main.rs). Without a responding SD card,
    # SdCard::init()'s ACMD41 polling loop (firmware/src/sdcard.rs) retries up
    # to 1000 times with a 2ms delay each -- tens of millions of cycles just to
    # time out. Attach a real (fast-responding) SD model, as test_soc_fast.py
    # does, so init succeeds quickly instead of exhausting worst-case retries.
    # (start_soc() attaches it with a 0x55AA MBR in sector 0.) This test never
    # shortened the POR, so keep it that way.
    ser, _ = await start_soc(dut, btn=0, accelerate_por=False)
    dut._log.info("GLS: SoC Reset released (Safe Mode S2 held).")

    # Even with a fast-responding SD model, SdCard::ensure_init() still runs a
    # real (slow, 400 kHz) SPI handshake before the Safe Mode check. Release S2 as
    # soon as Safe Mode is announced: the main loop treats a held S2 as "launch Slot 1".
    await ser.wait_for(SAFE_MODE.encode(), timeout_cycles=3_000_000)
    dut.btn.value = 1
    dut._log.info("GLS: Safe Mode entered")

    # The rest of the boot (banner, help, prompt) must come out intact from the netlist
    out = (await ser.wait_for(b"vux> ", timeout_cycles=3_000_000)).decode("utf-8", errors="replace")
    for line in (BANNER, "Available Commands:"):
        assert line in out, f"GLS: missing {line!r} in boot output {out!r}"
    dut._log.info("GLS: Synthesized netlist boots the Boot Manager to its prompt")
