# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

async def send_uart_byte(dut, byte_val: int, cnt: int = 434):
    """Helper to drive serial UART frame on rxd"""
    # 1. Start bit (0)
    dut.rxd.value = 0
    for _ in range(cnt):
        await FallingEdge(dut.clk)

    # 2. 8 Data bits (LSB first)
    for i in range(8):
        dut.rxd.value = (byte_val >> i) & 1
        for _ in range(cnt):
            await FallingEdge(dut.clk)

    # 3. Stop bit (1)
    dut.rxd.value = 1
    for _ in range(cnt):
        await FallingEdge(dut.clk)

@cocotb.test()
async def test_uart_rx(dut):
    """Test UART RX serial frame reception and ready pulse generation"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.rxd.value = 1

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)

    # 2. Send Byte 1: 0xA5 (0b10100101)
    test_byte1 = 0xA5
    cocotb.start_soon(send_uart_byte(dut, test_byte1))

    # Wait for rdy assertion
    while int(dut.rdy.value) == 0:
        await FallingEdge(dut.clk)

    assert int(dut.data.value) == test_byte1, f"Expected 0x{test_byte1:02X}, got 0x{int(dut.data.value):02X}"
    dut._log.info(f"Received Byte 1 successfully: 0x{test_byte1:02X}")

    # Wait for rdy to clear
    await FallingEdge(dut.clk)

    # 3. Send Byte 2: 0x3C (0b00111100)
    test_byte2 = 0x3C
    cocotb.start_soon(send_uart_byte(dut, test_byte2))

    while int(dut.rdy.value) == 0:
        await FallingEdge(dut.clk)

    assert int(dut.data.value) == test_byte2, f"Expected 0x{test_byte2:02X}, got 0x{int(dut.data.value):02X}"
    dut._log.info(f"Received Byte 2 successfully: 0x{test_byte2:02X}")

    dut._log.info("UART RX verified successfully [PASS]")
