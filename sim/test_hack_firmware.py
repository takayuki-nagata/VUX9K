# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Test suite for Hack 16-bit C Firmware execution on VUX9K SoC Python Emulator.
Verifies mode auto-detection, execution, math computations, array manipulation, and UART MMIO output.
"""

import os
import pytest
from emulator import SocEmulator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HACK_BIN = os.path.join(REPO_ROOT, "build_hack", "firmware.bin")

@pytest.fixture(scope="module")
def hack_emulator():
    """Load Hack firmware binary on SocEmulator and execute until completion."""
    if not os.path.exists(HACK_BIN):
        pytest.skip(f"Hack firmware binary not found at {HACK_BIN}. Build with 'make build-hack' first.")

    emu = SocEmulator()
    emu.load_binary(HACK_BIN)
    assert emu.detect_mode() is False, "Auto-mode detector should detect Hack mode"

    steps = emu.run(max_steps=50000, target_str="ALL HACK C FIRMWARE TESTS PASSED (100%)!\n")
    output = emu.get_tx_output()
    return emu, output

def test_hack_banner_output(hack_emulator):
    """Verify that firmware banner is emitted."""
    _, output = hack_emulator
    assert "VUX9K SoC Hack 16-bit C Firmware Test" in output

def test_hack_factorial(hack_emulator):
    """Verify Factorial(6) = 720 computation."""
    _, output = hack_emulator
    assert "Factorial(6) = 720 ... [PASS]" in output

def test_hack_fibonacci(hack_emulator):
    """Verify Fibonacci(10) = 55 computation."""
    _, output = hack_emulator
    assert "Fibonacci(10) = 55 ... [PASS]" in output

def test_hack_array_sum(hack_emulator):
    """Verify Array Sum = 150 computation."""
    _, output = hack_emulator
    assert "Array Sum = 150 ... [PASS]" in output

def test_hack_all_passed(hack_emulator):
    """Verify overall test completion summary."""
    _, output = hack_emulator
    assert "ALL HACK C FIRMWARE TESTS PASSED (100%)!" in output
