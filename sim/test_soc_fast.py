# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Fast Top-Level SoC Verification Testbench (test_soc_fast.py)
Verifies power-on reset, CPU boot, instruction execution, and initial peripheral activation.
"""

import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock
import os
import sys
import struct

sys.path.append(os.path.dirname(__file__))
from virtual_serial import VirtualSerialBridge
from sdcard_model import SpiSdCardModel

VUX_MAGIC = 0x56555839


@cocotb.test()
async def test_soc_fast_boot(dut):
    """Verify top-level SoC power-on reset, SD card loader, boot prompt, and S2 button launch"""
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    # Initialize external pins
    dut.rst_n.value = 0
    dut.btn.value = 1
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    # Attach SD Card SPI Model
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Preload Slot 1 (LBA 128) with a minimal test payload:
    # 64-byte VUX9 v2 header + instructions
    payload = bytes([
        0x13, 0x05, 0x20, 0x04,  # addi a0, zero, 0x42
        0x6F, 0x00, 0x00, 0x00,  # j .
    ])
    header = struct.pack("<IIII32s16s", VUX_MAGIC, 1, len(payload), 1, b"TestApp\x00".ljust(32, b"\x00"), b"\x00" * 16)
    sd_model.preload_sector(128, header + payload)

    ser = VirtualSerialBridge(dut, baud_cycles=234)

    # Assert active-low reset
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    # Accelerate POR in simulation
    if hasattr(dut, "por_counter"):
        dut.por_counter.value = (1 << 19)
    await ClockCycles(dut.clk, 10)
    dut._log.info("SoC Reset released.")

    # Wait for UART boot banner and prompt 'vux> '
    buf = b""
    cycles = 0
    last_report = 0
    while cycles < 2500000:
        if ser.in_waiting:
            c = ser.read(ser.in_waiting)
            buf += c
            if b"vux> " in buf:
                break
        await ClockCycles(dut.clk, 234)
        cycles += 234
        if cycles - last_report >= 50000:
            last_report = cycles
            pc = int(dut.cpu_inst.pc_out.value) if hasattr(dut, "cpu_inst") else 0
            dut._log.info(f"Cycles: {cycles}, PC: 0x{pc:08X}, buf: {buf[-40:]!r}")

    assert b"vux> " in buf, f"Failed to receive boot prompt from UART. Output: {buf.decode('utf-8', errors='replace')!r}"
    dut._log.info("SoC Fast Boot & UART Prompt verified successfully!")

    # Test S2 button press to launch Slot 1
    dut._log.info("Testing S2 button launch for Slot 1...")
    dut.btn.value = 0  # Button S2 pressed (active-low on pin)
    await ClockCycles(dut.clk, 27000 * 25)  # Hold for 25ms (passes 20ms debounce)
    dut.btn.value = 1  # Release button
    await ClockCycles(dut.clk, 27000 * 15)

    # Wait for Resident Loader "[RL] Slot 1" confirmation
    btn_buf = b""
    cycles = 0
    while cycles < 1000000:
        if ser.in_waiting:
            c = ser.read(ser.in_waiting)
            btn_buf += c
            if b"[RL] Slot 1" in btn_buf:
                break
        await ClockCycles(dut.clk, 234)
        cycles += 234

    assert b"[RL] Slot 1" in btn_buf, f"Button S2 launch failed. Output: {btn_buf.decode('utf-8', errors='replace')!r}"
    dut._log.info("S2 button launch of Slot 1 verified successfully!")
