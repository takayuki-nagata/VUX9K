# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (make build-zephyr-irq-echo, board vux9k) on the
emulator's real profile, launched from SD slot 1 like the Rust demo
(sim/emu/test_zephyr_demo.py). Its main thread sleeps until the UART callback wakes it,
so an echo at all means the RX interrupt (MEI) works; a burst longer than both 32-byte
FIFOs checks that RX keeps up and that TX refills from the driver's timer.
"""

import os

import pytest
from vux9k import REPO_ROOT, slot_image, start_soc, wait_for

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
I_RAM_FOR_PROGRAMS = 0x3800  # below the Resident Loader
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))


@pytest.fixture(scope="module")
def echo_bin():
    if not os.path.exists(ECHO_BIN):
        pytest.skip(f"{ECHO_BIN} missing (make build-zephyr-irq-echo; needs Zephyr)")
    with open(ECHO_BIN, "rb") as f:
        return f.read()


def test_fits_the_real_board(echo_bin):
    assert len(echo_bin) <= I_RAM_FOR_PROGRAMS


def test_echoes_a_burst_by_interrupt(echo_bin):
    slot1, _ = slot_image(echo_bin, slot=1, mode="riscv", name="IRQ echo")
    soc = start_soc(sd_sectors=slot1)
    wait_for(soc, b"vux> ", timeout_cycles=3_000_000)
    soc.uart_send(b"1")
    wait_for(soc, b"irq_echo ready\n", timeout_cycles=10_000_000)
    soc.run(200_000)  # past uart_irq_rx_enable
    for _ in range(2):  # the second burst: the driver state after the first
        mark = len(soc.uart_received())
        soc.uart_send(BURST + b"\n")
        wait_for(soc, b"err=", timeout_cycles=5_000_000, start=mark)
        soc.run(100_000)  # the error code and the newline
        out = soc.uart_received()[mark:].decode("utf-8", errors="replace")
        assert out == BURST.upper().decode() + "\nrx=64 drop=0 err=0\n", out
