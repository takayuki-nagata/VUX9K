# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
preloaded into the PSRAM and started by a jump from the ROM like the Rust demo
(sim/integration/test_soc_zephyr_demo.py; the emulator counterpart is
sim/emu/test_zephyr_irq_echo.py). Its interrupt handlers run from the PSRAM as well; with the
instruction cache in front of it they keep up with a line sent back to back at 115200 baud.
The paced line (irq_echo_pace) is what the board test sends. A line that arrives before
irq_echo enables RX overflows the 32-byte RX FIFO: its 33rd byte is lost, with no error to
report (uart_err_check is 0). The ROM holds irq_echo back until it has
(irq_echo_pace.hold_words), which leaves no race with its start-up time.
"""

import os

import cocotb
import irq_echo_pace as pace
from soc_env import (
    REPO_ROOT,
    UART_BAUD_CYCLES,
    XMEM_BASE,
    load_imem,
    load_xmem,
    preload_psram_app,
    soc_of,
    start_soc,
    wait_cycles,
)

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))
ECHO = BURST.upper() + b"\nrx=64 drop=0 err=0\n"
RX_FIFO = pace.RX_FIFO
EARLY = BURST[: pace.OVERRUN_LINE]  # overflows the RX FIFO by one byte
BRIDGE_BYTE_BITS = 11  # VirtualSerialBridge sends two stop bits


def echo_image() -> bytes:
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        return f.read()


async def start(dut):
    """The SoC with irq_echo preloaded and ready for input; returns the serial bridge."""
    ser, _ = await start_soc(dut, during_reset=preload_psram_app(echo_image()))
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
    """A line that arrives before irq_echo enables RX overflows the RX FIFO; the line keeps its
    first 32 bytes, the 33rd is lost, and uart_err_check has nothing to report (the UART has
    no error flags). The ROM holds irq_echo back until then, whatever its start-up time"""
    image = echo_image()

    def preload(soc):
        load_xmem(soc, XMEM_BASE, image)
        load_imem(soc, pace.hold_words(XMEM_BASE))

    ser, _ = await start_soc(dut, during_reset=preload)
    ser.write(EARLY)
    # The precondition, read before the hold ends: a test that lost it says so, instead of
    # reading as a wrong echo. The whole line has left the host and the RX FIFO is full, and
    # nothing has read it (the ROM's wait doesn't touch the UART): its 33rd byte was dropped.
    await wait_cycles(pace.HOLD_CYCLES - 2 * pace.SLOWEST_FRAME_CYCLES)
    soc = soc_of(dut)
    assert ser.tx_idle, "the line hasn't left the host before irq_echo starts"
    assert int(soc.uart_inst.rx_full.value) == 1, "the line hasn't filled the RX FIFO before irq_echo starts"
    assert not ser.tx_buffer and ser.in_waiting == 0, "irq_echo started before the line had arrived"
    await ser.wait_for(b"irq_echo ready\n", timeout_cycles=10_000_000)
    ser.reset_input_buffer()
    await wait_cycles(1_000_000)  # past uart_irq_rx_enable and the handler emptying the FIFO
    ser.write(b"\n")
    out = await reply(ser)
    assert out == BURST[:RX_FIFO].upper() + f"\nrx={RX_FIFO} drop=0 err=0\n".encode(), out
