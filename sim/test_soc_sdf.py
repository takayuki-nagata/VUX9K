# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb Post-PnR Timing Verification with SDF Back-Annotation (test_soc_sdf.py)
Verifies setup/hold timing margin and boot sequence on physical netlist.
"""

import os
import sys
import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock

sys.path.append(os.path.dirname(__file__))
from virtual_serial import VirtualSerialBridge
from sdcard_model import SpiSdCardModel

UART_BAUD_CYCLES = 234  # 27.0 MHz / 115200 baud


@cocotb.test()
async def test_soc_sdf_timing_and_boot(dut):
    """Run timing-annotated post-PnR simulation"""
    # 27.0 MHz Clock (period = 37038 ps)
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    # Attach SD Card SPI Model
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Initialize pins
    dut.rst_n.value = 1
    dut.btn.value = 1
    dut.uart_rx.value = 1

    # Attach Virtual Serial Bridge
    ser = VirtualSerialBridge(dut, baud_cycles=UART_BAUD_CYCLES)

    dut._log.info("=== SDF Simulation Started. Testing timing and Power-on Reset ===")
    await ClockCycles(dut.clk, 535000)

    # Test Prompt Response
    dut._log.info("Sending newline to sync prompt under SDF timing...")
    ser.write(b"\n")
    
    # Wait and check prompt
    buf = b""
    for _ in range(5000):
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
            if b"vux> " in buf:
                break
        await ClockCycles(dut.clk, 200)

    dut._log.info(f"SDF Serial output: {buf.decode('utf-8', errors='replace')!r}")
    assert b"vux> " in buf, "SDF Timing Simulation failed prompt synchronization!"
    dut._log.info("[PASS] SDF Post-PnR Timing Simulation successfully synchronized with Boot Manager!")
