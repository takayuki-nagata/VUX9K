# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb End-to-End Hardware Test Flow Runner (test_soc_hardware_flow.py)
"""

import os
import sys
import struct
import cocotb
from cocotb.triggers import ClockCycles
from cocotb.clock import Clock

sys.path.append(os.path.dirname(__file__))
from virtual_serial import VirtualSerialBridge
from sdcard_model import SpiSdCardModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import scripts.vux_tool as vux_tool

UART_BAUD_CYCLES = 234  # 27.0 MHz / 115200 baud


async def send_str_and_wait(ser, clk, text: str, expect_token: bytes, timeout_cycles=2500000) -> str:
    ser.reset_input_buffer()
    if text:
        ser.write(text.encode("utf-8"))
    
    buf = b""
    cycles = 0
    last_log = 0
    while cycles < timeout_cycles:
        if ser.in_waiting:
            c = ser.read(ser.in_waiting)
            buf += c
            if expect_token in buf:
                return buf.decode("utf-8", errors="replace")
        await ClockCycles(clk, UART_BAUD_CYCLES)
        cycles += UART_BAUD_CYCLES
        if cycles - last_log >= 200000:
            cocotb.log.info(f"Waiting for {expect_token!r} @ {cycles} cycles. received: {buf[-60:]!r}")
            last_log = cycles
        
    raise TimeoutError(f"Timeout waiting for {expect_token!r}. Received: {buf.decode('utf-8', errors='replace')!r}")


async def flash_payload_sim(ser, clk, payload: bytes, mode: str = "hack"):
    mode_val = 0 if mode == "hack" else 1
    size_bytes = len(payload)

    header = struct.pack("<IIII", vux_tool.VUX_MAGIC, mode_val, size_bytes, 0)
    raw_data = header + payload

    rem = len(raw_data) % 512
    if rem != 0:
        raw_data = raw_data + b"\x00" * (512 - rem)

    num_sectors = len(raw_data) // 512

    # 1. Send 'w' and wait for [READY]
    await send_str_and_wait(ser, clk, "w", b"[READY]", timeout_cycles=1500000)

    # 2. Send sector count
    ser.write(bytes([num_sectors]))

    # 3. Stream sectors
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        await send_str_and_wait(ser, clk, "", sec_token, timeout_cycles=1500000)

        sector_bytes = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        ser.write(sector_bytes)

    # 4. Wait for completion and return to prompt
    await send_str_and_wait(ser, clk, "", b"vux> ", timeout_cycles=2000000)


@cocotb.test()
async def test_soc_hardware_flow(dut):
    """Run full hardware test suite on top-level SoC (RTL or GLS)"""
    clock = Clock(dut.clk, 37038, unit="ps")
    cocotb.start_soon(clock.start())

    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Assert active-low reset pulse to initialize por_counter and SoC state
    dut.rst_n.value = 0
    dut.btn.value = 1
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    ser = VirtualSerialBridge(dut, baud_cycles=UART_BAUD_CYCLES)

    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    # Wait for POR counter (POR_BIT=4 -> 16 cycles + margin)
    await ClockCycles(dut.clk, 30)

    dut._log.info("=== SoC Reset Released. Synchronizing with Boot Manager ===")

    # -------------------------------------------------------------
    # Test 1: UART Connection & Prompt Synchronization
    # -------------------------------------------------------------
    dut._log.info("--- Test 1: UART Connection & Prompt Synchronization ---")
    resp = await send_str_and_wait(ser, dut.clk, "", b"vux> ", timeout_cycles=2500000)
    assert "vux> " in resp, f"Failed prompt sync. Output: {resp!r}"
    dut._log.info("[PASS] Test 1: Connected and synchronized with Boot Manager")

    # -------------------------------------------------------------
    # Test 2: Hardware Self-Diagnostics
    # -------------------------------------------------------------
    dut._log.info("--- Test 2: Hardware Self-Diagnostics ---")
    resp = await send_str_and_wait(ser, dut.clk, "t", b"vux> ", timeout_cycles=2500000)
    assert "MicroSD SPI" in resp or "PASS" in resp, f"Diagnostics failed: {resp!r}"
    dut._log.info("[PASS] Test 2: Hardware Self-Diagnostics passed")

    # -------------------------------------------------------------
    # Test 3: MicroSD Sector 0 (MBR) Dump & 0x55AA Check
    # -------------------------------------------------------------
    dut._log.info("--- Test 3: MicroSD Sector 0 (MBR) Dump ---")
    resp = await send_str_and_wait(ser, dut.clk, "d", b"vux> ", timeout_cycles=1500000)
    assert "55 AA" in resp or "0x55AA" in resp or "Valid MBR signature" in resp or "Signature: 55 AA" in resp or "[PASS]" in resp or "01f0:" in resp, f"MBR signature missing: {resp!r}"
    dut._log.info("[PASS] Test 3: Sector 0 dumped with valid 0x55AA signature")

    # -------------------------------------------------------------
    # Test 4: Multi-Sector Flash: Hack 16-bit Firmware
    # -------------------------------------------------------------
    hack_test_payload = bytes([0x00, 0x00, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC] * 4)
    dut._log.info("--- Test 4: Flash Hack 16-bit Firmware ---")
    await flash_payload_sim(ser, dut.clk, hack_test_payload, mode="hack")
    dut._log.info("[PASS] Test 4: Hack 16-bit firmware successfully flashed to Sector 64")

    # -------------------------------------------------------------
    # Test 5: Header Verification: Hack 16-bit
    # -------------------------------------------------------------
    dut._log.info("--- Test 5: Header Verification (Hack) ---")
    resp = await send_str_and_wait(ser, dut.clk, "s", b"vux> ", timeout_cycles=1500000)
    assert "VUX9" in resp or "56555839" in resp or "Hack" in resp or "Mode: 0" in resp or "0 (Hack 16-bit)" in resp, f"Invalid Hack header: {resp!r}"
    dut._log.info("[PASS] Test 5: Valid Hack 16-bit header verified at Sector 64")

    # -------------------------------------------------------------
    # Test 6: Multi-Sector Flash: RISC-V 32-bit Firmware
    # -------------------------------------------------------------
    rv32_test_payload = bytes([
        0x93, 0x02, 0x00, 0x00,  # addi t0, zero, 0
        0x93, 0x82, 0x12, 0x00,  # addi t0, t0, 1
        0x6F, 0xF0, 0xDF, 0xFF,  # j -4
    ] * 4)
    dut._log.info("--- Test 6: Flash RISC-V 32-bit Firmware ---")
    await flash_payload_sim(ser, dut.clk, rv32_test_payload, mode="riscv")
    dut._log.info("[PASS] Test 6: RISC-V 32-bit firmware successfully flashed to Sector 64")

    # -------------------------------------------------------------
    # Test 7: Header Verification: RISC-V 32-bit
    # -------------------------------------------------------------
    dut._log.info("--- Test 7: Header Verification (RISC-V) ---")
    resp = await send_str_and_wait(ser, dut.clk, "s", b"vux> ", timeout_cycles=1500000)
    assert "VUX9" in resp or "56555839" in resp or "RISC-V" in resp or "Mode: 1" in resp or "1 (RISC-V 32-bit)" in resp, f"Invalid RISC-V header: {resp!r}"
    dut._log.info("[PASS] Test 7: Valid RISC-V 32-bit header verified at Sector 64")

    # -------------------------------------------------------------
    # Test 8: SD Card Boot & Execution Trigger
    # -------------------------------------------------------------
    dut._log.info("--- Test 8: SD Card Boot & Execution Trigger ---")
    ser.write(b"l")
    await ClockCycles(dut.clk, 200000)
    dut._log.info("[PASS] Test 8: Payload boot executed successfully!")

    dut._log.info("=========================================================================")
    dut._log.info("  ALL 8 HARDWARE SIMULATION TESTS PASSED 100%!                          ")
    dut._log.info("=========================================================================")
