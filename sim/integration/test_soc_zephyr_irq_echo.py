# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
preloaded into the PSRAM and started by a jump from I-RAM like the Rust demo
(sim/integration/test_soc_zephyr_demo.py; the emulator counterpart is
sim/emu/test_zephyr_irq_echo.py). Its interrupt handlers run from the PSRAM as well; with the
instruction cache in front of it they keep up with a line sent back to back at 115200 baud.
The paced line (irq_echo_pace) is what the board test sends. An overrun still has to reach
uart_err_check: a line sent while irq_echo starts, before it enables RX, overflows the
32-byte RX FIFO.
"""

import os

import cocotb
import irq_echo_pace as pace
from soc_env import REPO_ROOT, UART_BAUD_CYCLES, preload_psram_app, start_soc, wait_cycles

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))
ECHO = BURST.upper() + b"\nrx=64 drop=0 err=0\n"
UART_ERROR_OVERRUN = 1  # Zephyr's enum uart_rx_stop_reason
RX_FIFO = 32  # soc's UART RX FIFO
BRIDGE_BYTE_BITS = 11  # VirtualSerialBridge sends two stop bits


async def start(dut, early: bytes = b""):
    """The SoC with irq_echo preloaded and ready for input; returns the serial bridge. early
    is sent from irq_echo's first cycle on, while it starts."""
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        image = f.read()
    ser, _ = await start_soc(dut, during_reset=preload_psram_app(image))
    if early:
        ser.write(early)
    await ser.wait_for(b"irq_echo ready\n", timeout_cycles=10_000_000)
    ser.reset_input_buffer()
    return ser


async def reply(ser) -> bytes:
    """The echo and its rx/drop/err line."""
    out = await ser.wait_for(b"err=", timeout_cycles=10_000_000)
    # The error code and the newline (TX from the PSRAM is slow); wait_for may have read them
    if b"\n" not in out[out.index(b"err=") :]:
        out += await ser.wait_for(b"\n", timeout_cycles=5_000_000)
    return out


# ~0.8M cycles to "ready" and well under 1M per line from the PSRAM (estimated from the
# I-RAM build's instruction counts, to be measured); the waits allow 40M (2.2 s simulated)
@cocotb.test(timeout_time=2500, timeout_unit="ms")
async def test_zephyr_irq_echo_from_the_psram(dut):
    """A 64-byte line (over both FIFOs), sent in paced chunks, comes back upper-cased with no overrun"""
    ser = await start(dut)
    for _ in range(2):  # the second line: the driver state after the first
        for chunk in pace.chunks(BURST + b"\n"):
            ser.write(chunk)
            bits = len(chunk) * BRIDGE_BYTE_BITS + pace.GAP_FRAMES * pace.FRAME_BITS
            await wait_cycles(bits * UART_BAUD_CYCLES)
        out = await reply(ser)
        assert out == ECHO, out


@cocotb.test(timeout_time=2000, timeout_unit="ms")
async def test_zephyr_irq_echo_back_to_back(dut):
    """A 64-byte line sent back to back comes back whole"""
    ser = await start(dut)
    ser.write(BURST + b"\n")
    out = await reply(ser)
    assert out == ECHO, out


@cocotb.test(timeout_time=2000, timeout_unit="ms")
async def test_zephyr_irq_echo_overrun_while_it_starts(dut):
    """A line sent while irq_echo starts, before it enables RX, overflows the RX FIFO; the line
    keeps its first 32 bytes, and uart_err_check reports the overrun"""
    ser = await start(dut, early=BURST)
    await wait_cycles(1_000_000)  # past uart_irq_rx_enable and the handler emptying the FIFO
    ser.write(b"\n")
    out = await reply(ser)
    assert out == BURST[:RX_FIFO].upper() + f"\nrx={RX_FIFO} drop=0 err={UART_ERROR_OVERRUN}\n".encode(), out
