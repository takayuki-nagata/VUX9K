# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
preloaded into the PSRAM and started by a jump from I-RAM like the Rust demo
(sim/integration/test_soc_zephyr_demo.py; the emulator counterpart is
sim/emu/test_zephyr_irq_echo.py). Its interrupt handlers run from the PSRAM as well, too slow
for a line sent back to back at 115200 baud: the line is sent in paced chunks
(irq_echo_pace), and the back-to-back line is an expected failure until the cache (B4a),
which must still report the overrun.
"""

import os
import re

import cocotb
import irq_echo_pace as pace
from soc_env import REPO_ROOT, UART_BAUD_CYCLES, preload_psram_app, start_soc, wait_cycles

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))
ECHO = BURST.upper() + b"\nrx=64 drop=0 err=0\n"
UART_ERROR_OVERRUN = 1  # Zephyr's enum uart_rx_stop_reason
BRIDGE_BYTE_BITS = 11  # VirtualSerialBridge sends two stop bits


async def start(dut):
    """The SoC with irq_echo preloaded and ready for input; returns the serial bridge."""
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        image = f.read()
    ser, _ = await start_soc(dut, during_reset=preload_psram_app(image))
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


# The reply to BURST sent back to back, for test_zephyr_irq_echo_back_to_back
_back_to_back: list[bytes] = []


@cocotb.test(timeout_time=2000, timeout_unit="ms")
async def test_zephyr_irq_echo_back_to_back_reports_the_overrun(dut):
    """A 64-byte line sent back to back loses bytes in the RX FIFO, and uart_err_check reports the overrun"""
    ser = await start(dut)
    # The overrun may drop any byte of the line, its newline too, and irq_echo answers only at
    # a newline: that is sent alone once the handler has read what the RX FIFO kept (at most
    # 32 bytes; the time for the whole line, twice, is the margin)
    ser.write(BURST)
    drain_frames = 2 * pace.HANDLER_FRAMES_PER_BYTE * len(BURST)
    await wait_cycles((len(BURST) * BRIDGE_BYTE_BITS + drain_frames * pace.FRAME_BITS) * UART_BAUD_CYCLES)
    ser.write(b"\n")
    out = await reply(ser)
    _back_to_back.append(out)
    m = re.fullmatch(rb"([A-Z0-9]*)\nrx=(\d+) drop=0 err=(\d+)\n", out)
    assert m, out
    assert len(m.group(1)) == int(m.group(2)) < len(BURST), out
    assert int(m.group(3)) == UART_ERROR_OVERRUN, out


# Strict: a pass fails the run. The reply of the test above, no simulation of its own.
@cocotb.test(expect_fail=True)
async def test_zephyr_irq_echo_back_to_back(dut):
    """A 64-byte line sent back to back comes back whole

    Code runs from the uncached PSRAM; the cache (B4a) should bring it back."""
    if not _back_to_back:  # not an assertion: expect_fail would take it for the failure
        raise RuntimeError("test_zephyr_irq_echo_back_to_back_reports_the_overrun got no reply")
    assert _back_to_back[0] == ECHO, _back_to_back[0]
