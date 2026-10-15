# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
preloaded into the PSRAM and started by a jump from I-RAM like the Rust demo
(sim/integration/test_soc_zephyr_demo.py; the emulator counterpart is
sim/emu/test_zephyr_irq_echo.py). Its interrupt handlers run from the PSRAM too.
"""

import os

import cocotb
from soc_env import REPO_ROOT, preload_psram_app, start_soc

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))


# ~0.8M cycles to "ready" and well under 1M per burst from the PSRAM (estimated from the
# I-RAM build's instruction counts, to be measured); the waits allow 30M (1.7 s simulated)
@cocotb.test(timeout_time=2500, timeout_unit="ms")
async def test_zephyr_irq_echo_from_the_psram(dut):
    """A 64-byte burst (over both FIFOs) comes back upper-cased with no overrun"""
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        image = f.read()
    ser, _ = await start_soc(dut, during_reset=preload_psram_app(image))
    await ser.wait_for(b"irq_echo ready\n", timeout_cycles=10_000_000)
    ser.reset_input_buffer()
    for _ in range(2):  # the second burst: the driver state after the first
        ser.write(BURST + b"\n")
        # A nonzero error code times out, and the TimeoutError shows the output
        out = await ser.wait_for(b"err=0\n", timeout_cycles=10_000_000)
        assert out == BURST.upper() + b"\nrx=64 drop=0 err=0\n", out
