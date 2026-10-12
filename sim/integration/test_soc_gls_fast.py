# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Gate-Level SoC Verification Testbench (test_soc_gls_fast.py)
Boots the synthesized netlist with S2 held and checks that the first stage stays at its
prompt (its banner and help intact).
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import BANNER, S1_PROMPT, start_soc

SAFE_MODE = "[SAFE MODE] Button S2 held: staying at the prompt."


@cocotb.test()
async def test_soc_gls_fast_boot(dut):
    """Verify synthesized gate-level SoC netlist boot and initial UART transmission"""
    # S2 held through reset: the first stage reads it right after its banner and stays at
    # its prompt, without touching the card. This test never shortened the POR, so keep
    # it that way.
    ser, _ = await start_soc(dut, btn=0, accelerate_por=False)
    dut._log.info("GLS: SoC Reset released (S2 held).")
    out = (await ser.wait_for(S1_PROMPT, timeout_cycles=3_000_000)).decode("utf-8", errors="replace")
    dut.btn.value = 1
    for line in (BANNER, SAFE_MODE, "Commands:"):
        assert line in out, f"GLS: missing {line!r} in boot output {out!r}"
    dut._log.info("GLS: Synthesized netlist boots the first stage to its prompt")
