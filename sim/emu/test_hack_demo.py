# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Hack 16-bit C/asm demo (hack_demo/) on the emulator, started two ways with the same
checks: as test_soc_hack starts it on the RTL (vux9k.start_hack: its words in Hack's I
segment, lockstep_programs.hack_stub soft-resetting into Hack), and as the board starts
it, from the card: the second stage's `run VUX9K/APPS/HACK.BIN` (make apps) loads it into
the I segment and soft-resets in Hack mode. At its end the demo soft-resets without a
mode, back to RV32: to the stub, or to the first stage.
(Ported from sim/emulator/test_hack_firmware.py.)
"""

import os

import pytest
from s1_env import BANNER, run_from_card
from vux9k import HACK_BACK, REPO_ROOT, read_hex_words, start_hack, wait_for

HACK_HEX = os.path.join(REPO_ROOT, "build", "hack", "firmware.hex")
DONE = b"ALL HACK C FIRMWARE TESTS PASSED (100%)!"
CLOSING = "=" * 41 + "\n"


@pytest.fixture(scope="module", params=["stub", "s2_run"])
def hack_run(request):
    assert os.path.exists(HACK_HEX), f"{HACK_HEX} missing; run `make build-hack` first"
    if request.param == "stub":
        soc, mark = start_hack(read_hex_words(HACK_HEX)), 0
    else:
        soc, mark = run_from_card("HACK.BIN")
    out = wait_for(soc, DONE, timeout_cycles=20_000_000, start=mark)
    riscv_at_done = soc.riscv_mode
    # The closing rule (~0.5M cycles at the demo's pace), then its soft reset
    out += wait_for(soc, CLOSING.encode(), timeout_cycles=2_000_000, start=mark + len(out.encode()))
    soc.run(100_000)
    return request.param, soc, out, riscv_at_done, len(soc.uart_received()[: mark + len(out.encode())])


def test_hack_runs_in_hack_mode(hack_run):
    _, _, _, riscv_at_done, _ = hack_run
    assert not riscv_at_done


@pytest.mark.parametrize(
    "line",
    [
        "VUX9K SoC Hack 16-bit C Firmware Test",
        "Factorial(6) = 720 ... [PASS]",
        "Fibonacci(10) = 55 ... [PASS]",
        "Array Sum = 150 ... [PASS]",
    ],
)
def test_hack_report_line(hack_run, line):
    _, _, out, _, _ = hack_run
    assert line in out


def test_hack_no_failures(hack_run):
    _, _, out, _, _ = hack_run
    assert "[FAIL]" not in out


def test_hack_demo_soft_resets_to_rv32(hack_run):
    """After the report the demo soft-resets without a mode: RV32 at 0, the stub (which prints
    HACK_BACK) or the first stage (its banner)."""
    how, soc, out, _, end = hack_run
    assert out.endswith(CLOSING), out[-60:]
    assert soc.riscv_mode
    after = soc.uart_received()[end:]
    if how == "stub":
        assert after == bytes([HACK_BACK]), after
    else:
        soc.run(2_000_000)
        assert BANNER.encode() in soc.uart_received()[end:], soc.uart_received()[end:][:200]
