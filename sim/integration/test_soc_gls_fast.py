# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Gate-Level SoC Verification Testbench (test_soc_gls_fast.py)
Verifies power-on reset release, gate-level netlist execution, and initial UART activity in GLS.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
from soc_env import start_soc


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

    # Wait for first character from UART (Safe Mode banner starts with newline).
    # Even with a fast-responding SD model, SdCard::ensure_init() still runs a
    # real (slow, 400 kHz) SPI handshake before Safe Mode's check short-circuits
    # the rest of check_boot_manager_update() -- test_soc_fast.py's reference
    # trace (full non-Safe-Mode path, includes an extra Slot 0 SD read) needs
    # ~650,000 cycles to its first byte, so budget comfortably above that.
    buf = await ser.wait_any(timeout_cycles=900000)

    dut.btn.value = 1  # Release button S2

    assert len(buf) > 0 or dut.uart_tx.value == 0, f"No UART activity detected in GLS netlist. Output: {buf!r}"
    dut._log.info("GLS: Synthesized netlist boot and UART transmission verified successfully!")
