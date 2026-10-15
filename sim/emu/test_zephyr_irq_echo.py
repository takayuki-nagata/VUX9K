# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (make build-zephyr-irq-echo, board vux9k) on the
emulator's real profile, started like the Rust demo (sim/emu/test_zephyr_demo.py): loaded
into the PSRAM, and from the card by the second stage's `run VUX9K/APPS/IRQECHO.BIN`. Its
main thread sleeps until the UART callback wakes it, so an echo at all means the RX
interrupt (MEI) works; a burst longer than both 32-byte FIFOs checks that RX keeps up and
that TX refills from the driver's timer, with the handlers running from the PSRAM.
"""

import os

import pytest
from s1_env import STAGE1_DIR, STAGE2_BASE, run_from_card
from vux9k import REPO_ROOT, XMEM_BASE, new_soc, read_hex_words, wait_for

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
FLASH_BYTES = 4 * 1024 * 1024  # the board's flash (vux9k.dts), from XMEM_BASE
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))


@pytest.fixture(scope="module")
def echo_bin():
    if not os.path.exists(ECHO_BIN):
        pytest.skip(f"{ECHO_BIN} missing (make build-zephyr-irq-echo; needs Zephyr)")
    with open(ECHO_BIN, "rb") as f:
        return f.read()


def test_fits_the_board_and_the_run_window(echo_bin):
    assert len(echo_bin) <= min(FLASH_BYTES, STAGE2_BASE - XMEM_BASE)


@pytest.mark.parametrize("how", ["load_app", "s2_run"])
def test_echoes_a_burst_by_interrupt(echo_bin, how):
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
    for _ in range(2):  # the second burst: the driver state after the first
        mark = len(soc.uart_received())
        soc.uart_send(BURST + b"\n")
        wait_for(soc, b"err=", timeout_cycles=10_000_000, start=mark)
        soc.run(100_000)  # the error code and the newline
        out = soc.uart_received()[mark:].decode("utf-8", errors="replace")
        assert out == BURST.upper().decode() + "\nrx=64 drop=0 err=0\n", out
