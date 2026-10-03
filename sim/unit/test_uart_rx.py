# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer


async def send_uart_byte(dut, byte_val: int, cnt: int = 234, stop_bit: int = 1):
    """Helper to drive serial UART frame on rxd at 27MHz / 115200bps (CNT=234)"""
    # 1. Start bit (0)
    dut.rxd.value = 0
    for _ in range(cnt):
        await FallingEdge(dut.clk)

    # 2. 8 Data bits (LSB first)
    for i in range(8):
        dut.rxd.value = (byte_val >> i) & 1
        for _ in range(cnt):
            await FallingEdge(dut.clk)

    # 3. Stop bit (1, or 0 to provoke a framing error)
    dut.rxd.value = stop_bit
    for _ in range(cnt):
        await FallingEdge(dut.clk)
    dut.rxd.value = 1


@cocotb.test()
async def test_uart_rx(dut):
    """Test UART RX serial frame reception and ready pulse generation"""
    clock = Clock(dut.clk, 37038, unit="ps")  # 27.0 MHz integer period in ps
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.rxd.value = 1

    await ClockCycles(dut.clk, 10)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)

    cnt = int(dut.CNT.value) if hasattr(dut, "CNT") else 434
    dut._log.info(f"Using UART baud cnt = {cnt}")

    # 2. Send Byte 1: 0xA5 (0b10100101)
    test_byte1 = 0xA5
    cocotb.start_soon(send_uart_byte(dut, test_byte1, cnt=cnt))

    # Wait for rdy assertion
    while int(dut.rdy.value) == 0:
        await FallingEdge(dut.clk)

    assert int(dut.data.value) == test_byte1, f"Expected 0x{test_byte1:02X}, got 0x{int(dut.data.value):02X}"
    dut._log.info(f"Received Byte 1 successfully: 0x{test_byte1:02X}")

    # Wait for frame completion and idle line
    await ClockCycles(dut.clk, 300)

    # 3. Send Byte 2: 0x3C (0b00111100)
    test_byte2 = 0x3C
    cocotb.start_soon(send_uart_byte(dut, test_byte2, cnt=cnt))

    while int(dut.rdy.value) == 0:
        await FallingEdge(dut.clk)

    assert int(dut.data.value) == test_byte2, f"Expected 0x{test_byte2:02X}, got 0x{int(dut.data.value):02X}"
    dut._log.info(f"Received Byte 2 successfully: 0x{test_byte2:02X}")

    dut._log.info("UART RX serial frame reception test passed 100% [PASS]")


async def reset_rx(dut):
    cocotb.start_soon(Clock(dut.clk, 37038, unit="ps").start())
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.rxd.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    return int(dut.CNT.value) if hasattr(dut, "CNT") else 434


async def receive(dut, cycles: int):
    """(data, frame_err) of the first rdy pulse within cycles, or None."""
    for _ in range(cycles):
        await FallingEdge(dut.clk)
        if int(dut.rdy.value):
            return int(dut.data.value), int(dut.frame_err.value)
    return None


@cocotb.test()
async def test_uart_rx_rejects_short_glitch(dut):
    """A low pulse shorter than half a bit is not a start bit: no byte is received"""
    cnt = await reset_rx(dut)
    dut.rxd.value = 0
    await ClockCycles(dut.clk, cnt // 4)
    dut.rxd.value = 1
    assert await receive(dut, 12 * cnt) is None, "a glitch on an idle line produced a byte"

    # The receiver is back in IDLE and takes the next real frame
    cocotb.start_soon(send_uart_byte(dut, 0x5A, cnt=cnt))
    assert await receive(dut, 12 * cnt) == (0x5A, 0)


@cocotb.test()
async def test_uart_rx_framing_error(dut):
    """A stop bit sampled low is flagged with the byte (frame_err on the rdy pulse)"""
    cnt = await reset_rx(dut)
    cocotb.start_soon(send_uart_byte(dut, 0xC3, cnt=cnt, stop_bit=0))
    assert await receive(dut, 12 * cnt) == (0xC3, 1)
    await ClockCycles(dut.clk, 2 * cnt)  # line back to idle
    cocotb.start_soon(send_uart_byte(dut, 0x3C, cnt=cnt))
    assert await receive(dut, 12 * cnt) == (0x3C, 0)


PERIOD_PS = 37038  # reset_rx's clock


async def send_frame_timed(dut, byte_val, bit_ps, stop_bit=1, idle_ps=0):
    """One frame with the given bit length (a sender whose baud rate is off), then idle"""
    for level in [0] + [(byte_val >> i) & 1 for i in range(8)] + [stop_bit]:
        dut.rxd.value = level
        await Timer(bit_ps, unit="ps")
    dut.rxd.value = 1
    if idle_ps:
        await Timer(idle_ps, unit="ps")


def _rate(ratio):
    return "slow" if ratio > 1.01 else "fast" if ratio < 0.99 else "nominal"


def _idle(idle_bits):
    return "none" if idle_bits == 0 else "half" if idle_bits < 1 else "bits"


# Data bytes all 0/all 1, both stop bit levels, a sender slow/fast/on the baud rate, and
# the idle gaps before the next frame
@fcov.point(
    "uart_rx.byte", ("0x00", "0xff", "other"), xf=lambda b, bad, r, i: {0: "0x00", 0xFF: "0xff"}.get(b, "other")
)
@fcov.point("uart_rx.stop", (0, 1), xf=lambda b, bad, r, i: int(not bad))
@fcov.point("uart_rx.rate", ("slow", "nominal", "fast"), xf=lambda b, bad, r, i: _rate(r))
@fcov.point("uart_rx.idle", ("none", "half", "bits"), xf=lambda b, bad, r, i: _idle(i))
def sample(byte_val, bad_stop, ratio, idle_bits):
    pass


@cocotb.test()
async def test_uart_rx_random_frames(dut):
    """Random bytes back to back, from a sender up to 3% off the baud rate, with random
    idle gaps (none to two bits) and some low stop bits: every byte arrives once, in
    order, with frame_err exactly on the bad ones"""
    cnt = await reset_rx(dut)
    got = []

    async def monitor():
        # rdy is combinational: Icarus shows its zero-width glitches as edges, so a pulse
        # counts only if rdy is still high at the falling clock edge
        while True:
            await RisingEdge(dut.rdy)
            await FallingEdge(dut.clk)
            if int(dut.rdy.value):
                got.append((int(dut.data.value), int(dut.frame_err.value)))

    cocotb.start_soon(monitor())
    sent = []
    nominal = cnt * PERIOD_PS
    for i in range(60):
        byte_val, bad_stop = random.getrandbits(8), random.random() < 0.15
        if i < 2:
            byte_val = (0x00, 0xFF)[i]  # all-0 and all-1 data bits, which random bytes rarely are
        # A low stop bit from a slow sender stays low for more than half a bit after the
        # receiver has sampled it, which is (rightly) a new start bit: keep those at or
        # above the baud rate
        bit_ps = round(nominal * random.uniform(0.97, 1.0 if bad_stop else 1.03))
        # After a low stop bit the line must be high for a while before the next start bit
        idle = random.randint(1, 2) * nominal if bad_stop else random.choice((0, 0, nominal // 2, 2 * nominal))
        sample(byte_val, bad_stop, bit_ps / nominal, idle / nominal)
        await send_frame_timed(dut, byte_val, bit_ps, stop_bit=0 if bad_stop else 1, idle_ps=idle)
        sent.append((byte_val, int(bad_stop)))
    await Timer(2 * nominal, unit="ps")
    assert got == sent, f"received {len(got)} frames, sent {len(sent)}; first difference at " + str(
        next((i for i, (g, s) in enumerate(zip(got, sent, strict=False)) if g != s), min(len(got), len(sent)))
    )
    fcov.export()
