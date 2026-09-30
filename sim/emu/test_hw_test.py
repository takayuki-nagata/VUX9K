# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""firmware/hw_test on the emulator: every checksum must match (make hw-smoke's reference).

The checksums hw_test compares against were taken from the emulator, which the lockstep
tests tie to the RTL; this keeps them from drifting when the firmware or toolchain
changes. On the board a failing line points at a timing failure (README, "Clock").
"""

import os
import re

from vux9k import FIRMWARE_DIR, read_hex_words, start_soc, wait_for

HWTEST_HEX = os.path.join(FIRMWARE_DIR, "hw_test", "hw_test.hex")


def test_hw_test_passes_on_the_emulator():
    soc = start_soc(imem_words=read_hex_words(HWTEST_HEX), card=False)
    out = wait_for(soc, b"RESULT", timeout_cycles=200_000_000)
    out += wait_for(soc, b"\n", timeout_cycles=1_000_000, start=len(out))
    lines = [ln for ln in out.splitlines() if ln.startswith("T")]
    assert len(lines) == 11, out
    assert all(ln.endswith(" PASS") for ln in lines), out
    assert re.search(r"RESULT PASS 0B/0B 00000000$", out, re.M), out
