# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (build/zephyr-demo, board vux9k) on the RTL, preloaded into the PSRAM
where its board links it, and started by a jump from I-RAM 0 in place of the first stage
(soc_env.preload_psram_app): the second stage's `run` starts it the same way, after loading
it from the card, which is test_soc_zephyr_s2.py (GLS, test-slow). The emulator counterpart
is sim/emu/test_zephyr_demo.py. Verilator RTL only: the preload writes the PSRAM word model.
"""

import os
import re

import cocotb
from soc_env import REPO_ROOT, preload_psram_app, start_soc

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")


# ~3M cycles from the PSRAM through its caches (the emulator's), waited for up to 40M
# (2.2 s simulated)
@cocotb.test(timeout_time=3000, timeout_unit="ms")
async def test_zephyr_rust_demo_from_the_psram(dut):
    """Zephyr boots from the PSRAM, runs the Rust app, and k_msleep(100) takes 100 ms"""
    assert os.path.exists(DEMO_BIN), f"{DEMO_BIN} missing; run `make build-zephyr-demo`"
    with open(DEMO_BIN, "rb") as f:
        image = f.read()
    ser, _ = await start_soc(dut, during_reset=preload_psram_app(image))
    out = await ser.wait_for(b"All Rust application tasks finished successfully!", timeout_cycles=40_000_000)
    out = out.decode("utf-8", errors="replace")
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out, out
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    assert m, out
    dut._log.info(f"k_msleep(100) took {m.group(1)} ms")
    # A tick (1 ms at the boards' 1000 ticks/s) plus rounding, as from I-RAM (from the
    # uncached PSRAM it read 103-104); 100 ticks/s reads 110 or more
    assert 100 <= int(m.group(1)) <= 102, out
