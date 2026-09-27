# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer


async def loopback_wire(dut):
    """Continuously loop back txd to rxd (X/Z-safe for GLS)"""
    while True:
        try:
            dut.rxd.value = int(dut.txd.value) & 1
        except ValueError:
            dut.rxd.value = 1  # Treat X/Z as idle (high) like real hardware pull-up
        await FallingEdge(dut.clk)


@cocotb.test()
async def test_uart_controller(dut):
    """Test UART Controller full duplex TX -> RX FIFO loopback communication"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    cocotb.start_soon(loopback_wire(dut))

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.clr_err.value = 0
    dut.wdata.value = 0
    dut.rxd.value = 1

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Wait until reset state machine settles to IDLE
    for _ in range(500):
        await FallingEdge(dut.clk)

    assert int(dut.empty.value) == 1, "RX FIFO should initially be empty"
    assert int(dut.full.value) == 0, "TX FIFO should initially not be full"

    # 2. Write 3 bytes to TX FIFO: 0x48 ('H'), 0x69 ('i'), 0x21 ('!')
    test_bytes = [0x48, 0x69, 0x21]
    for b in test_bytes:
        dut.wdata.value = b
        dut.we.value = 1
        await FallingEdge(dut.clk)
    dut.we.value = 0

    # 3. Read back each byte from RX FIFO after transmission over loopback
    received_bytes = []
    for expected_byte in test_bytes:
        # Wait until byte arrives in RX FIFO (empty == 0)
        timeout = 0
        while timeout < 20000:
            try:
                if int(dut.empty.value) == 0:
                    break
            except ValueError:
                pass  # X/Z values in GLS - keep waiting
            await FallingEdge(dut.clk)
            timeout += 1

        assert int(dut.empty.value) == 0, f"Timed out waiting for byte 0x{expected_byte:02X}"
        await Timer(1, unit="ns")
        rec_val = int(dut.rdata.value)
        received_bytes.append(rec_val)
        dut._log.info(f"Loopback received: 0x{rec_val:02X} ('{chr(rec_val)}')")

        # Pop from RX FIFO
        dut.re.value = 1
        await FallingEdge(dut.clk)
        dut.re.value = 0
        await FallingEdge(dut.clk)

    assert received_bytes == test_bytes, f"Data mismatch! Expected {test_bytes}, got {received_bytes}"
    assert int(dut.empty.value) == 1, "RX FIFO should be empty after reading all bytes"

    dut._log.info("UART Controller full loopback test passed 100% [PASS]")


async def send_frame(dut, byte_val: int, cnt: int, stop_bit: int = 1):
    """Drive one 8N1 frame on rxd (bit time = cnt clocks)."""
    for bit in [0] + [(byte_val >> i) & 1 for i in range(8)] + [stop_bit]:
        dut.rxd.value = bit
        for _ in range(cnt):
            await FallingEdge(dut.clk)
    dut.rxd.value = 1


@cocotb.test()
async def test_uart_controller_error_flags(dut):
    """overrun (byte dropped on a full RX FIFO) and frame_err are sticky until clr_err"""
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.clr_err.value = 0
    dut.wdata.value = 0
    dut.rxd.value = 1
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst.value = 1
    for _ in range(500):
        await FallingEdge(dut.clk)
    cnt = int(dut.CNT.value) if hasattr(dut, "CNT") else 434

    assert (int(dut.overrun.value), int(dut.frame_err.value)) == (0, 0)

    # 32 bytes fill the RX FIFO; the 33rd is dropped
    for i in range(33):
        await send_frame(dut, i, cnt)
    for _ in range(cnt):
        await FallingEdge(dut.clk)
    assert int(dut.overrun.value) == 1, "33rd byte into a 32-deep RX FIFO must flag an overrun"
    assert int(dut.frame_err.value) == 0

    dut.clr_err.value = 1
    await FallingEdge(dut.clk)
    dut.clr_err.value = 0
    assert int(dut.overrun.value) == 0, "clr_err must clear overrun"

    # Drain the FIFO, then a frame with a low stop bit
    dut.re.value = 1
    for _ in range(32):
        await FallingEdge(dut.clk)
    dut.re.value = 0
    assert int(dut.empty.value) == 1
    await send_frame(dut, 0x55, cnt, stop_bit=0)
    for _ in range(cnt):
        await FallingEdge(dut.clk)
    assert int(dut.frame_err.value) == 1, "a low stop bit must flag a framing error"
    assert int(dut.overrun.value) == 0
    dut.clr_err.value = 1
    await FallingEdge(dut.clk)
    dut.clr_err.value = 0
    assert int(dut.frame_err.value) == 0, "clr_err must clear frame_err"
