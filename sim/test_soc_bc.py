# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Test suite for Zephyr RTOS & bc_clone_rs execution on VUX9K SoC Python Emulator.
Verifies all arbitrary-precision mathematical self-tests and interactive REPL command execution.
"""

import os
import pytest
from emulator import SocEmulator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZEPHYR_BIN = os.path.join(REPO_ROOT, "build_zephyr", "zephyr", "zephyr.bin")

@pytest.fixture(scope="module")
def booted_bc_emulator():
    """Boot Zephyr bc_clone_rs application on SocEmulator and wait for self-tests to complete."""
    if not os.path.exists(ZEPHYR_BIN):
        pytest.skip(f"Zephyr binary not found at {ZEPHYR_BIN}. Build with 'make build-zephyr' first.")

    emu = SocEmulator()
    emu.load_binary(ZEPHYR_BIN)

    # Run until self-test suite completes and REPL prompt appears
    output = emu.run(max_steps=50000000, target_str="bc> ")
    assert "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!" in output, (
        f"Self-tests did not pass within step limit. Got:\n{output}"
    )
    assert "bc> " in output, f"REPL prompt 'bc> ' not reached. Got:\n{output}"
    return emu

def test_soc_bc_self_tests(booted_bc_emulator):
    """Verify that all 10 mathematical test cases passed successfully."""
    output = booted_bc_emulator.get_tx_output()
    assert "Basic Arithmetic & Precedence" in output
    assert "Scale Division" in output
    assert "BigInt Power (2^100)" in output
    assert "Recursive Factorial f(20)" in output
    assert "Transcendental Pi: 4 * a(1)" in output
    assert "Transcendental Exp: e(1)" in output
    assert "Transcendental Log: l(2.718281828459045)" in output
    assert "Base Conversion (Hex to Binary)" in output
    assert "Arrays and Dynamic Auto Scoping" in output
    assert "Streaming Callback Test: 2^16 = 65536" in output
    assert "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!" in output

def test_soc_bc_repl_arithmetic(booted_bc_emulator):
    """Verify evaluating 2^32 in interactive REPL."""
    emu = booted_bc_emulator
    emu.clear_tx_output()
    emu.feed_input("2^32\r\n")
    out = emu.run(max_steps=5000000, target_str="bc> ")
    assert "4294967296" in out, f"Expected 4294967296 in REPL output, got:\n{out}"

def test_soc_bc_repl_custom_function(booted_bc_emulator):
    """Verify defining and invoking a custom user function in interactive REPL."""
    emu = booted_bc_emulator
    emu.clear_tx_output()
    emu.feed_input("define cube(x) { return (x^3); }\r\n")
    emu.run(max_steps=5000000, target_str="bc> ")

    emu.clear_tx_output()
    emu.feed_input("cube(5)\r\n")
    out = emu.run(max_steps=5000000, target_str="bc> ")
    assert "125" in out, f"Expected 125 in REPL output, got:\n{out}"

def test_soc_bc_repl_scale_division(booted_bc_emulator):
    """Verify precision division with scale in interactive REPL."""
    emu = booted_bc_emulator
    emu.clear_tx_output()
    emu.feed_input("scale = 6; 22 / 7\r\n")
    out = emu.run(max_steps=5000000, target_str="bc> ")
    assert "3.142857" in out, f"Expected 3.142857 in REPL output, got:\n{out}"
