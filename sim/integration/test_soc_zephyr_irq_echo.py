# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
preloaded into I-RAM like the Rust demo (sim/integration/test_soc_zephyr_demo.py; the
emulator counterpart is sim/emu/test_zephyr_irq_echo.py).
"""

import os

import cocotb
from soc_env import REPO_ROOT, load_imem, start_soc

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))


@cocotb.test()
async def test_zephyr_irq_echo_from_i_ram(dut):
    """A 64-byte burst (over both FIFOs) comes back upper-cased with no overrun"""
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        image = f.read()
    words = [int.from_bytes(image[i : i + 4].ljust(4, b"\0"), "little") for i in range(0, len(image), 4)]
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, words))
    await ser.wait_for(b"irq_echo ready\n", timeout_cycles=10_000_000)
    ser.reset_input_buffer()
    for _ in range(2):  # the second burst: the driver state after the first
        ser.write(BURST + b"\n")
        # A nonzero error code times out, and the TimeoutError shows the output
        out = await ser.wait_for(b"err=0\n", timeout_cycles=5_000_000)
        assert out == BURST.upper() + b"\nrx=64 drop=0 err=0\n", out
