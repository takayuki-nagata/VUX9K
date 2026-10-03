# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Edge, FallingEdge, Timer


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


@cocotb.test()
async def test_uart_controller_random_loopback(dut):
    """Random bursts into the TX FIFO (some while it is full: dropped), random reads of the
    RX FIFO and pauses, over a txd->rxd loopback: the bytes read are exactly the accepted
    ones, in order, with no overrun or framing error"""
    period_ns = 20
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    for sig in ("we", "re", "clr_err", "wdata"):
        getattr(dut, sig).value = 0
    dut.rxd.value = 1
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    async def loopback():  # txd is a register output: no glitches to follow
        while True:
            await Edge(dut.txd)
            dut.rxd.value = dut.txd.value

    cocotb.start_soon(loopback())
    frame_ns = 10 * 434 * period_ns  # uart_controller's default CNT
    accepted, received = [], []

    async def read_all():
        while not int(dut.empty.value):
            received.append(int(dut.rdata.value))
            dut.re.value = 1
            await FallingEdge(dut.clk)
            dut.re.value = 0
            await Timer(1, unit="ns")

    while len(accepted) < 60:
        r = random.random()
        if r < 0.4:  # a burst, possibly past full
            for _ in range(random.randint(1, 40)):
                data = random.getrandbits(8)
                dut.wdata.value = data
                dut.we.value = 1
                await Timer(1, unit="ns")
                if not int(dut.full.value):
                    accepted.append(data)
                await FallingEdge(dut.clk)
            dut.we.value = 0
        elif r < 0.7:
            await read_all()
        else:
            await Timer(random.randint(1, 3 * frame_ns), unit="ns")
            await FallingEdge(dut.clk)
            await read_all()  # often enough that the RX FIFO can't overflow
    for _ in range(len(accepted) - len(received) + 2):  # the rest is at most one frame each
        await Timer(frame_ns, unit="ns")
        await FallingEdge(dut.clk)
        await read_all()
    assert received == accepted, f"accepted {len(accepted)} bytes, read {len(received)}"
    assert (int(dut.overrun.value), int(dut.frame_err.value)) == (0, 0)
