# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
bc_clone_rs on Zephyr (make build-zephyr, board vux9k/vux9k/ext) on the emulator's
extended profile: 512 KB/256 KB, NOT real hardware (bc needs ~260 KB). The image is
loaded at address 0 directly. Self-tests, then the interactive REPL.
(Ported from sim/emulator/test_soc_bc.py.)
"""

import os

import pytest
from vux9k import REPO_ROOT, Soc, send_paced, wait_for

ZEPHYR_BIN = os.path.join(REPO_ROOT, "build", "zephyr", "zephyr", "zephyr.bin")
SELF_TESTS = [
    "Basic Arithmetic & Precedence",
    "Scale Division",
    "BigInt Power (2^100)",
    "Recursive Factorial f(20)",
    "Transcendental Pi: 4 * a(1)",
    "Transcendental Exp: e(1)",
    "Transcendental Log: l(2.718281828459045)",
    "Base Conversion (Hex to Binary)",
    "Arrays and Dynamic Auto Scoping",
    "Streaming Callback Test: 2^16 = 65536",
    "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!",
]


@pytest.fixture(scope="module")
def bc():
    if not os.path.exists(ZEPHYR_BIN):
        pytest.skip(f"{ZEPHYR_BIN} missing (make build-zephyr; needs Zephyr)")
    soc = Soc("extended")
    assert "NOT REAL HARDWARE" in soc.profile
    with open(ZEPHYR_BIN, "rb") as f:
        soc.load_iram(0, f.read())
    boot = wait_for(soc, b"bc> ", timeout_cycles=2_000_000_000)
    return soc, boot


def repl(soc, line: str) -> str:
    start = len(soc.uart_received())
    send_paced(soc, line.encode() + b"\r")  # Enter, as a terminal sends it
    return wait_for(soc, b"bc> ", timeout_cycles=200_000_000, start=start)


@pytest.mark.parametrize("line", SELF_TESTS)
def test_self_test(bc, line):
    _, boot = bc
    assert line in boot


def test_repl_power(bc):
    assert "4294967296" in repl(bc[0], "2^32")


def test_repl_function(bc):
    repl(bc[0], "define cube(x) { return (x^3); }")
    assert "125" in repl(bc[0], "cube(5)")


def test_repl_scale_division(bc):
    assert "3.142857" in repl(bc[0], "scale = 6; 22 / 7")
