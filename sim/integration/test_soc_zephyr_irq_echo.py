# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (build/zephyr-irq-echo, board vux9k) on the RTL,
from SD slot 1 through the Boot Manager's '1' and the Resident Loader, as on the board
(the emulator counterpart is sim/emu/test_zephyr_irq_echo.py).
"""

import os

import cocotb
from soc_env import REPO_ROOT, slot_image, start_soc

ECHO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-irq-echo", "zephyr", "zephyr.bin")
BURST = bytes(b"abcdefghijklmnopqrstuvwxyz0123456789"[i % 36] for i in range(64))


@cocotb.test()
async def test_zephyr_irq_echo_from_sd(dut):
    """A 64-byte burst (over both FIFOs) comes back upper-cased with no overrun"""
    assert os.path.exists(ECHO_BIN), f"{ECHO_BIN} missing; run `make build-zephyr-irq-echo`"
    with open(ECHO_BIN, "rb") as f:
        slot1, _ = slot_image(f.read(), slot=1, mode="riscv", name="IRQ echo")
    ser, _ = await start_soc(dut, sd_sectors=slot1)
    await ser.wait_for(b"vux> ", timeout_cycles=3_000_000)
    ser.write(b"1")
    await ser.wait_for(b"irq_echo ready\n", timeout_cycles=10_000_000)
    ser.reset_input_buffer()
    for _ in range(2):  # the second burst: the driver state after the first
        ser.write(BURST + b"\n")
        # A nonzero error code times out, and the TimeoutError shows the output
        out = await ser.wait_for(b"err=0\n", timeout_cycles=5_000_000)
        assert out == BURST.upper() + b"\nrx=64 drop=0 err=0\n", out
