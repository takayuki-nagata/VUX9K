# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Gate-Level SoC Verification Testbench (test_soc_gls_fast.py)
Verifies power-on reset release, gate-level netlist execution, and initial UART activity in GLS.
"""

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock
import os
import sys

sys.path.append(os.path.dirname(__file__))
from virtual_serial import VirtualSerialBridge
from sdcard_model import SpiSdCardModel


@cocotb.test()
async def test_soc_gls_fast_boot(dut):
    """Verify synthesized gate-level SoC netlist boot and initial UART transmission"""
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    # Initialize external pins (hold Button S2 for Boot Manager Safe Mode)
    dut.rst_n.value = 0
    dut.btn.value = 0
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    # `main()` unconditionally calls SdCard::ensure_init() before the Safe Mode
    # check (see firmware/src/main.rs). Without a responding SD card,
    # SdCard::init()'s ACMD41 polling loop (firmware/src/sdcard.rs) retries up
    # to 1000 times with a 2ms delay each -- tens of millions of cycles just to
    # time out. Attach a real (fast-responding) SD model, as test_soc_fast.py
    # does, so init succeeds quickly instead of exhausting worst-case retries.
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())
    mbr = bytearray(512)
    mbr[510] = 0x55
    mbr[511] = 0xAA
    sd_model.preload_sector(0, bytes(mbr))

    ser = VirtualSerialBridge(dut, baud_cycles=234)

    # Assert active-low reset
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)
    dut._log.info("GLS: SoC Reset released (Safe Mode S2 held).")

    # Wait for first character from UART (Safe Mode banner starts with newline).
    # Even with a fast-responding SD model, SdCard::ensure_init() still runs a
    # real (slow, 400 kHz) SPI handshake before Safe Mode's check short-circuits
    # the rest of check_boot_manager_update() -- test_soc_fast.py's reference
    # trace (full non-Safe-Mode path, includes an extra Slot 0 SD read) needs
    # ~650,000 cycles to its first byte, so budget comfortably above that.
    buf = b""
    cycles = 0
    while cycles < 900000:
        if ser.in_waiting:
            c = ser.read(ser.in_waiting)
            buf += c
            if len(buf) > 0:
                break
        await ClockCycles(dut.clk, 234)
        cycles += 234

    dut.btn.value = 1  # Release button S2

    assert len(buf) > 0 or dut.uart_tx.value == 0, f"No UART activity detected in GLS netlist. Output: {buf!r}"
    dut._log.info("GLS: Synthesized netlist boot and UART transmission verified successfully!")
