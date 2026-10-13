# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Hack 16-bit C/asm demo (hack_demo/) on the emulator, loaded like test_soc_hack
does on the RTL: its words over I-RAM at power-on, the ISA auto-detected.
(Ported from sim/emulator/test_hack_firmware.py.)
"""

import os

import pytest
from vux9k import REPO_ROOT, read_hex_words, start_soc, wait_for

HACK_HEX = os.path.join(REPO_ROOT, "build", "hack", "firmware.hex")
DONE = b"ALL HACK C FIRMWARE TESTS PASSED (100%)!"


@pytest.fixture(scope="module")
def hack_run():
    assert os.path.exists(HACK_HEX), f"{HACK_HEX} missing; run `make build-hack` first"
    soc = start_soc(imem_words=read_hex_words(HACK_HEX))
    out = wait_for(soc, DONE, timeout_cycles=20_000_000)
    # The closing rule, then main returns into the startup code's halt loop
    out += wait_for(soc, b"=\n", timeout_cycles=2_000_000, start=len(out.encode()))
    soc.run(100_000)
    return soc, out


def test_hack_mode_detected(hack_run):
    soc, _ = hack_run
    assert not soc.riscv_mode


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
    _, out = hack_run
    assert line in out


def test_hack_no_failures(hack_run):
    _, out = hack_run
    assert "[FAIL]" not in out


def test_hack_demo_returns_to_its_halt_loop(hack_run):
    soc, out = hack_run
    assert out.endswith("=" * 41 + "\n"), out[-60:]
    pc = soc.pc
    end = len(soc.uart_received())
    soc.run(10_000)
    assert soc.pc in (pc, pc + 1, pc - 1), "not in the 2-instruction halt loop"
    assert len(soc.uart_received()) == end, "output after the report"
