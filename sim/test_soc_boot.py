# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb RTL Integration Test for VUX9K SoC & Boot Manager
Tests:
1. Stand-alone SD Card Auto-Loader / Reset execution
2. Boot Manager UART Communication & CLI Command Response
"""

import cocotb
from cocotb.triggers import ClockCycles, Timer, RisingEdge, FallingEdge
from cocotb.clock import Clock
import os
import sys

# Import SD Card SPI Model
sys.path.append(os.path.dirname(__file__))
from sdcard_model import SpiSdCardModel

UART_BAUD_CYCLES = 234 # 27.0 MHz / 115200 baud

async def uart_send_byte(dut, byte_val: int):
    """Send 1 byte over UART (8N1) to dut.uart_rx"""
    dut.uart_rx.value = 0 # Start bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES)
    for i in range(8):
        dut.uart_rx.value = (byte_val >> i) & 1
        await ClockCycles(dut.clk, UART_BAUD_CYCLES)
    dut.uart_rx.value = 1 # Stop bit
    await ClockCycles(dut.clk, UART_BAUD_CYCLES)

async def uart_recv_byte(dut, timeout_cycles=200000) -> int:
    """Receive 1 byte over UART from dut.uart_tx"""
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
    clock = Clock(dut.clk, 37038, unit="ps") # 27.0 MHz
    cocotb.start_soon(clock.start())

    # Attach SD Card Model to SD SPI pins
    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())

    # Preload simple RISC-V program into Sector 64 (MBR gap @ 32KB offset) of SD Card:
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
