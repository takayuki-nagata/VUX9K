# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb RTL verification for VUX9K SoC executing the Hack 16-bit C/Asm firmware (hack_demo/).
Verifies Hack mode auto-detection (active_mode = 0), instruction execution and Hack UART MMIO
output, by checking the firmware's own self-test report on the UART.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import load_imem, read_hex_words, start_soc

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HACK_HEX = os.path.join(REPO_ROOT, "build", "hack", "firmware.hex")

# Same report the emulator test (sim/emu/test_hack_demo.py) expects
EXPECTED_LINES = [
    "VUX9K SoC Hack 16-bit C Firmware Test",
    "Factorial(6) = 720 ... [PASS]",
    "Fibonacci(10) = 55 ... [PASS]",
    "Array Sum = 150 ... [PASS]",
]
DONE = "ALL HACK C FIRMWARE TESTS PASSED (100%)!"


@cocotb.test()
async def test_soc_hack_firmware(dut):
    """Hack firmware runs from I-RAM and reports all self-tests passed over the UART"""
    assert os.path.exists(HACK_HEX), f"{HACK_HEX} missing; run `make build-hack` first"
    words = read_hex_words(HACK_HEX)

    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, words))

    mode = int(dut.soc.active_mode.value)
    assert mode == 0, f"SoC must auto-detect Hack 16-bit mode (active_mode=0), got {mode}"

    out = (await ser.wait_for(DONE.encode(), timeout_cycles=20_000_000)).decode("utf-8", errors="replace")
    for line in EXPECTED_LINES:
        assert line in out, f"missing {line!r} in Hack firmware output: {out!r}"
    assert "[FAIL]" not in out, f"Hack firmware reported a failure: {out!r}"
    dut._log.info("Hack 16-bit firmware self-test report verified over UART")
