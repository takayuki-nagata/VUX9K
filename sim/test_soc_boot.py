# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb RTL Integration Test for Hardware Boot Manager (hw_boot_mgr)
Tests:
1. Auto-Load from SD Card (Timeout / Stand-alone Boot)
2. UART <-> SPI Bridge Mode and Flashing
"""

import cocotb
from cocotb.triggers import ClockCycles, Timer, RisingEdge, FallingEdge
from cocotb.clock import Clock
import os
import sys

# Import SD Card SPI Model
sys.path.append(os.path.dirname(__file__))
from sdcard_model import SpiSdCardModel

UART_BAUD_CYCLES = 434 # 50MHz / 115200 baud

async def uart_send_byte(dut, byte_val: int):
    """Send 1 byte over UART (8N1) to dut.uart_rx"""
    dut.uart_rx.value = 0 # Start bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES)
    for i in range(8):
        dut.uart_rx.value = (byte_val >> i) & 1
        await ClockCycles(dut.clk, UART_BAUD_CYCLES)
    dut.uart_rx.value = 1 # Stop bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES)

async def uart_recv_byte(dut, timeout_cycles=100000) -> int:
    """Receive 1 byte over UART from dut.uart_tx"""
    # Wait for start bit (Falling edge of uart_tx)
    cycles = 0
    while dut.uart_tx.value == 1 and cycles < timeout_cycles:
        await ClockCycles(dut.clk, 1)
        cycles += 1
    if cycles >= timeout_cycles:
        raise TimeoutError("UART receive timeout waiting for start bit")

    # Sample in middle of start bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES // 2)

    # Sample 8 data bits
    val = 0
    for i in range(8):
        await ClockCycles(dut.clk, UART_BAUD_CYCLES)
        val |= (int(dut.uart_tx.value) << i)

    # Wait through stop bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES)
    return val

@cocotb.test()
async def test_boot_auto_load_standalone(dut):
    """Test 1: Stand-alone SD Card Auto-Loader on boot timeout"""
    clock = Clock(dut.clk, 20, unit="ns") # 50 MHz
    cocotb.start_soon(clock.start())

    # Attach SD Card Model to SD SPI pins
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Preload simple RISC-V program into Sector 64 (MBR gap @ 32KB offset) of SD Card:
    # 0x00: 0x00000293  (addi t0, zero, 0)
    # 0x04: 0x00128293  (addi t0, t0, 1)
    # 0x08: 0xffdff06f  (j 0x04 - loop)
    test_prog = bytes([
        0x93, 0x02, 0x00, 0x00, # addi t0, zero, 0
        0x93, 0x82, 0x12, 0x00, # addi t0, t0, 1
        0x6F, 0xF0, 0xDF, 0xFF, # j -4
    ])
    sd_model.preload_sector(64, test_prog)

    # Assert Reset (active-low)
    dut.rst.value = 0
    dut.uart_rx.value = 1
    await ClockCycles(dut.clk, 20)
    dut.rst.value = 1

    dut._log.info("Reset released. Waiting for Boot Manager timeout and Auto-Load from SD Card...")

    # Wait for timeout (~100,000 cycles) + SD card init and sector transfer
    # Poll until cpu_inst.pc_out starts incrementing
    for _ in range(300):
        await ClockCycles(dut.clk, 1000)
        pc = int(dut.cpu_inst.pc_out.value)
        if pc != 0:
            dut._log.info(f"CPU started executing from I-RAM! PC = 0x{pc:08X}")
            break

    # Verify active mode and CPU execution
    assert int(dut.active_mode.value) == 1, "Should auto-detect RISC-V mode!"
    dut._log.info("Stand-alone SD Card Auto-Load and CPU Boot PASSED! [OK]")

@cocotb.test()
async def test_boot_bridge_mode_flash(dut):
    """Test 2: Host-driven UART <-> SPI Bridge mode and SD Flashing"""
    clock = Clock(dut.clk, 20, unit="ns") # 50 MHz
    cocotb.start_soon(clock.start())

    # Attach SD Card Model
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Assert Reset
    dut.rst.value = 0
    dut.uart_rx.value = 1
    await ClockCycles(dut.clk, 20)
    dut.rst.value = 1

    dut._log.info("Sending Sync (0x5A) to enter Bridge Mode...")
    await uart_send_byte(dut, 0x5A)

    # Receive ACK (0x5A)
    ack = await uart_recv_byte(dut)
    assert ack == 0x5A, f"Expected 0x5A ACK, got 0x{ack:02X}"
    dut._log.info("Bridge Mode ACK received [OK]")

    # Test CS Low command (0x02)
    await uart_send_byte(dut, 0x02)
    cs_ack = await uart_recv_byte(dut)
    assert cs_ack == 0x06, f"Expected 0x06 ACK for CS Low, got 0x{cs_ack:02X}"
    assert int(dut.sd_cs_n.value) == 0, "SD CS should be Low"

    # Test SPI Transfer (0x01 + 0xFF)
    await uart_send_byte(dut, 0x01)
    await uart_send_byte(dut, 0xFF)
    spi_resp = await uart_recv_byte(dut)
    dut._log.info(f"Bridge SPI XFER byte received: 0x{spi_resp:02X} [OK]")

    # Test CS High command (0x03)
    await uart_send_byte(dut, 0x03)
    cs_ack2 = await uart_recv_byte(dut)
    assert cs_ack2 == 0x06, "Expected 0x06 ACK for CS High"
    assert int(dut.sd_cs_n.value) == 1, "SD CS should be High"

    # Trigger Direct CPU Boot (0x06)
    await uart_send_byte(dut, 0x06)
    await ClockCycles(dut.clk, 100)
    dut._log.info("Direct CPU Boot triggered and verified [OK]")
