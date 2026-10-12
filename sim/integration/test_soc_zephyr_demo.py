# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (build/zephyr-demo, board vux9k) on the RTL, preloaded into I-RAM in
place of the first stage: its board links it there, and starting it from the card is the
second stage's job once that loads applications (the emulator counterpart is
sim/emu/test_zephyr_demo.py).
"""

import os
import re

import cocotb
from soc_env import REPO_ROOT, load_imem, start_soc

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")


@cocotb.test()
async def test_zephyr_rust_demo_from_i_ram(dut):
    """Zephyr boots from I-RAM, runs the Rust app, and k_msleep(100) takes 100 ms"""
    assert os.path.exists(DEMO_BIN), f"{DEMO_BIN} missing; run `make build-zephyr-demo`"
    with open(DEMO_BIN, "rb") as f:
        image = f.read()
    words = [int.from_bytes(image[i : i + 4].ljust(4, b"\0"), "little") for i in range(0, len(image), 4)]
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, words))
    out = await ser.wait_for(b"All Rust application tasks finished successfully!", timeout_cycles=40_000_000)
    out = out.decode("utf-8", errors="replace")
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out, out
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    # A tick (1 ms at the boards' 1000 ticks/s) plus rounding; 100 ticks/s reads 110
    assert m and 100 <= int(m.group(1)) <= 102, out
