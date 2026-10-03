# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

BIT_CYCLES = 434  # uart_tx default CNT: clock cycles per bit


@cocotb.test()
async def test_uart_tx(dut):
    """Test UART TX serial frame transmission (Start bit, 8 data bits LSB-first, Stop bit)"""
    clock = Clock(dut.clk, 20, unit="ns")  # 50MHz
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.data.value = 0

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Wait until reset state machine settles to IDLE (busy = 0)
    while int(dut.busy.value) == 1:
        await FallingEdge(dut.clk)

    assert int(dut.txd.value) == 1, "TX line should be idle high (1)"

    # 2. Transmit bytes and check every frame bit: Start(0), D0..D7 (LSB first), Stop(1).
    # Neither byte is a palindrome or an alternating pattern, so bit-order and
    # off-by-one-bit errors change the sampled frame.
    for tx_byte in (0x4B, 0xD2):
        dut.data.value = tx_byte
        dut.we.value = 1
        await FallingEdge(dut.clk)
        dut.we.value = 0

        await Timer(1, unit="ns")
        assert int(dut.busy.value) == 1, "busy should assert after we=1"

        # Wait for the start bit's falling edge, then move to the middle of it
        while int(dut.txd.value) == 1:
            await FallingEdge(dut.clk)
        for _ in range(BIT_CYCLES // 2):
            await FallingEdge(dut.clk)

        expected_bits = [0] + [(tx_byte >> i) & 1 for i in range(8)] + [1]
        sampled_bits = []
        for _ in range(len(expected_bits)):
            sampled_bits.append(int(dut.txd.value))
            for _ in range(BIT_CYCLES):
                await FallingEdge(dut.clk)

        assert sampled_bits == expected_bits, (
            f"byte 0x{tx_byte:02X}: sampled frame {sampled_bits}, expected {expected_bits}"
        )

        # Transmission finishes and returns to IDLE (busy=0, txd=1)
        while int(dut.busy.value) == 1:
            await FallingEdge(dut.clk)
        assert int(dut.txd.value) == 1, "TX line should return to idle high"
        dut._log.info(f"UART TX frame verified: byte=0x{tx_byte:02X}")


@cocotb.test()
async def test_uart_tx_random_bytes(dut):
    """Random bytes, each written a random 0-3 cycles after busy drops (so frames can
    follow back to back): a receiver sampling mid-bit sees each one intact, in order"""
    period_ns = 20
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.data.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    while int(dut.busy.value) == 1:
        await FallingEdge(dut.clk)

    got = []

    async def receiver():
        bit_ns = BIT_CYCLES * period_ns
        while True:
            await FallingEdge(dut.txd)  # start bit
            await Timer(bit_ns // 2, unit="ns")
            assert int(dut.txd.value) == 0, "start bit not low at its middle"
            byte = 0
            for i in range(8):
                await Timer(bit_ns, unit="ns")
                byte |= int(dut.txd.value) << i
            await Timer(bit_ns, unit="ns")
            assert int(dut.txd.value) == 1, f"stop bit of 0x{byte:02X} not high"
            got.append(byte)

    cocotb.start_soon(receiver())
    sent = [random.getrandbits(8) for _ in range(25)]
    for byte in sent:
        for _ in range(random.randint(0, 3)):
            await FallingEdge(dut.clk)
        dut.data.value = byte
        dut.we.value = 1
        await FallingEdge(dut.clk)
        dut.we.value = 0
        await Timer(1, unit="ns")
        # busy is combinational (glitches on Icarus): skip most of the frame, then poll it
        await Timer(9 * BIT_CYCLES * period_ns, unit="ns")
        while int(dut.busy.value) == 1:
            await FallingEdge(dut.clk)
    await Timer(2 * BIT_CYCLES * period_ns, unit="ns")
    assert got == sent, f"sent {[hex(b) for b in sent]}, received {[hex(b) for b in got]}"
