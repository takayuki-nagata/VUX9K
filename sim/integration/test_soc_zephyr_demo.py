# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (build/zephyr-demo, board vux9k) on the RTL, from SD slot 1
through the Boot Manager's '1' and the Resident Loader, as on the board
(the emulator counterpart is sim/emu/test_zephyr_demo.py).
"""

import os
import re

import cocotb
from soc_env import REPO_ROOT, slot_image, start_soc

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")


@cocotb.test()
async def test_zephyr_rust_demo_from_sd(dut):
    """Zephyr boots from slot 1, runs the Rust app, and k_msleep(100) takes 100 ms"""
    assert os.path.exists(DEMO_BIN), f"{DEMO_BIN} missing; run `make build-zephyr-demo`"
    with open(DEMO_BIN, "rb") as f:
        slot1, _ = slot_image(f.read(), slot=1, mode="riscv", name="Zephyr Rust")
    ser, _ = await start_soc(dut, sd_sectors=slot1)
    await ser.wait_for(b"vux> ", timeout_cycles=3_000_000)
    ser.reset_input_buffer()
    ser.write(b"1")
    out = await ser.wait_for(b"All Rust application tasks finished successfully!", timeout_cycles=40_000_000)
    out = out.decode("utf-8", errors="replace")
    assert "[RL] Slot 1" in out and "Zephyr RTOS Booting on VUX9K SoC!" in out, out
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    assert m and 100 <= int(m.group(1)) <= 110, out
