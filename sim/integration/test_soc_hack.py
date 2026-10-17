# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb RTL verification for VUX9K SoC executing the Hack 16-bit C/Asm firmware (hack_demo/).
Started as a loader starts it (soc_env.start_hack: the program in Hack's I segment in the
PSRAM, a soft reset with the Hack boot mode), it runs from the window through its caches,
data in its D segment; checked by the firmware's own self-test report on the UART, then its
soft reset without a mode back to RV32 (the stub's HACK_BACK).
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from lockstep_programs import HACK_BACK
from soc_env import read_hex_words, soc_of, start_hack

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
    """Hack firmware runs from its PSRAM segments, reports its self-tests passed and soft-resets to RV32"""
    assert os.path.exists(HACK_HEX), f"{HACK_HEX} missing; run `make build-hack` first"
    ser, _ = await start_hack(dut, read_hex_words(HACK_HEX))

    # The report ends at ~4.1M cycles (the emulator's count)
    out = (await ser.wait_for(DONE.encode(), timeout_cycles=20_000_000)).decode("utf-8", errors="replace")
    for line in EXPECTED_LINES:
        assert line in out, f"missing {line!r} in Hack firmware output: {out!r}"
    assert "[FAIL]" not in out, f"Hack firmware reported a failure: {out!r}"
    dut._log.info("Hack 16-bit firmware self-test report verified over UART")

    # The closing rule, then the soft reset without a mode: RV32 at 0, where the stub prints
    back = "=" * 41 + "\n" + chr(HACK_BACK)
    out = (await ser.wait_for(back.encode(), timeout_cycles=2_000_000)).decode("utf-8", errors="replace")
    assert out.endswith(back), out
    mode = int(soc_of(dut).active_mode.value)
    assert mode == 1, f"the demo's soft reset must start RV32 (active_mode=1), got {mode}"
    dut._log.info("Hack demo soft-reset back to RV32")
