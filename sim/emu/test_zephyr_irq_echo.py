# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (make build-zephyr-irq-echo, board vux9k) on the
emulator's real profile, started like the Rust demo (sim/emu/test_zephyr_demo.py): loaded
into the PSRAM, and from the card by the second stage's `run VUX9K/APPS/IRQECHO.BIN`. Its
main thread sleeps until the UART callback wakes it, so an echo at all means the RX
interrupt (MEI) works; a line longer than both 32-byte FIFOs checks that RX keeps up and
that TX refills from the driver's timer, with the handlers running from the PSRAM.

From the uncached PSRAM the RX handler can't keep up with a line sent back to back at
115200 baud: the line is sent in paced chunks (irq_echo_pace), and the back-to-back line
is an expected failure until the cache (B4a), which must still report the overrun.
"""

import os
import re

import pytest
from s1_env import STAGE1_DIR, STAGE2_BASE, run_from_card
from vux9k import REPO_ROOT, UART_FRAME, XMEM_BASE, new_soc, read_hex_words, wait_for

# isort: split
import irq_echo_pace as pace  # noqa: E402 (sim/integration, on sys.path once vux9k is imported)

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
FLASH_BYTES = 4 * 1024 * 1024  # the board's flash (vux9k.dts), from XMEM_BASE
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))
ECHO = BURST.upper().decode() + "\nrx=64 drop=0 err=0\n"
UART_ERROR_OVERRUN = 1  # Zephyr's enum uart_rx_stop_reason
UNCACHED_PSRAM = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="code runs from the uncached PSRAM; the cache (B4a) should bring it back",
)


@pytest.fixture(scope="module")
def echo_bin():
    if not os.path.exists(ECHO_BIN):
        pytest.skip(f"{ECHO_BIN} missing (make build-zephyr-irq-echo; needs Zephyr)")
    with open(ECHO_BIN, "rb") as f:
        return f.read()


def test_fits_the_board_and_the_run_window(echo_bin):
    assert len(echo_bin) <= min(FLASH_BYTES, STAGE2_BASE - XMEM_BASE)


def start(echo_bin: bytes, how: str):
    """The SoC with irq_echo ready for input, from the PSRAM or through the second stage."""
    if how == "load_app":
        soc, mark = new_soc(), 0
        soc.load_app(echo_bin, "riscv", XMEM_BASE)
    else:
        soc, mark = run_from_card("IRQECHO.BIN")
        assert soc.xmem_word(XMEM_BASE) == int.from_bytes(echo_bin[:4], "little")
        stage1 = read_hex_words(f"{STAGE1_DIR}/firmware.hex")
        assert all(soc.iram_word(4 * i) == w for i, w in enumerate(stage1))
    # ~0.8M cycles from the PSRAM (an estimate from the I-RAM build's instruction counts)
    wait_for(soc, b"irq_echo ready\n", timeout_cycles=10_000_000, start=mark)
    soc.run(1_000_000)  # past uart_irq_rx_enable
    return soc


def reply(soc, mark: int) -> str:
    """The echo and its rx/drop/err line, from output index mark."""
    wait_for(soc, b"err=", timeout_cycles=10_000_000, start=mark)
    # The error code and the newline: TX from the PSRAM is slow, so a real wait, not a fixed run
    err = soc.uart_received().index(b"err=", mark)
    wait_for(soc, b"\n", timeout_cycles=5_000_000, start=err)
    end = soc.uart_received().index(b"\n", err) + 1
    return soc.uart_received()[mark:end].decode("utf-8", errors="replace")


def send_chunked(soc, data: bytes) -> int:
    """Send data as the tests pace it (irq_echo_pace: a chunk every CHUNK + GAP_FRAMES
    frame times); returns the longest time from a chunk's first byte until the program
    had emptied the RX FIFO, in cycles (what GAP_FRAMES must cover, with a margin)."""
    period = (pace.CHUNK + pace.GAP_FRAMES) * UART_FRAME
    busy = 0
    for chunk in pace.chunks(data):
        start = soc.cycle
        soc.uart_send(chunk)
        soc.run(max(0, soc.uart_send_done - soc.cycle))
        while soc.uart_rx_level and soc.cycle < start + period:
            soc.run(500)
        busy = max(busy, soc.cycle - start)
        soc.run(max(0, start + period - soc.cycle))
    return busy


@pytest.mark.parametrize("how", ["load_app", "s2_run"])
def test_echoes_a_paced_line_by_interrupt(echo_bin, how):
    soc = start(echo_bin, how)
    period = (pace.CHUNK + pace.GAP_FRAMES) * UART_FRAME
    for _ in range(2):  # the second line: the driver state after the first
        mark = len(soc.uart_received())
        busy = send_chunked(soc, BURST + b"\n")
        # The measurement behind irq_echo_pace.GAP_FRAMES
        print(
            f"[{how}] {pace.CHUNK}-byte chunks: RX FIFO empty {busy} cycles "
            f"({busy / UART_FRAME:.1f} frame times) after a chunk's first byte at worst; "
            f"a chunk every {period} ({period / busy:.2f}x)"
        )
        out = reply(soc, mark)
        assert out == ECHO, out
        # The gap's margin over the measurement (irq_echo_pace): a slower handler shows here
        assert period >= 2 * busy, f"a chunk every {period} cycles, read in {busy}"


_back_to_back: dict[str, str] = {}


def send_back_to_back(soc, line: bytes) -> None:
    """line sent back to back, then its newline alone once the program has emptied the RX
    FIFO: the overrun may drop any byte of the line, and irq_echo answers only at a newline."""
    soc.uart_send(line)
    soc.run(max(0, soc.uart_send_done - soc.cycle))
    end = soc.cycle + 2 * pace.HANDLER_FRAMES_PER_BYTE * len(line) * UART_FRAME  # step cap
    while soc.uart_rx_level and soc.cycle < end:
        soc.run(500)
    if soc.uart_rx_level:  # not an assertion: the xfail below would take it for the failure
        raise RuntimeError("irq_echo stopped reading the RX FIFO")
    soc.run(pace.CHUNK * UART_FRAME)  # margin: the handler done with the last byte
    soc.uart_send(b"\n")


def back_to_back_reply(echo_bin: bytes, how: str) -> str:
    """irq_echo's reply to BURST sent back to back (run once per `how`, for both tests)."""
    if how not in _back_to_back:
        soc = start(echo_bin, how)
        mark = len(soc.uart_received())
        send_back_to_back(soc, BURST)
        _back_to_back[how] = reply(soc, mark)
    return _back_to_back[how]


@pytest.mark.parametrize("how", ["load_app", "s2_run"])
def test_a_back_to_back_line_reports_the_overrun(echo_bin, how):
    """What the RX FIFO lost reaches uart_err_check: the driver keeps the sticky error bits
    that its own status reads clear."""
    out = back_to_back_reply(echo_bin, how)
    m = re.fullmatch(r"([A-Z0-9]*)\nrx=(\d+) drop=0 err=(\d+)\n", out)
    assert m, out
    assert len(m.group(1)) == int(m.group(2)) < len(BURST), out
    assert int(m.group(3)) == UART_ERROR_OVERRUN, out


@UNCACHED_PSRAM
@pytest.mark.parametrize("how", ["load_app", "s2_run"])
def test_echoes_a_back_to_back_line_by_interrupt(echo_bin, how):
    assert back_to_back_reply(echo_bin, how) == ECHO
