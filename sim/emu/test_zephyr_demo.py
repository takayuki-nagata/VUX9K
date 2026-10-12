# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (make build-zephyr-demo, board vux9k) on the emulator's real
profile, loaded into I-RAM as the image of an application (`vux9k-emu --load ... --mode
riscv`). Its board links it into I-RAM; starting it from the card is the second stage's
job, once that loads applications.
"""

import os
import re

import pytest
from vux9k import REPO_ROOT, new_soc, wait_for

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")
I_RAM_FOR_PROGRAMS = 0x3800  # the board's flash region (zephyr.md)


@pytest.fixture(scope="module")
def demo_bin():
    if not os.path.exists(DEMO_BIN):
        pytest.skip(f"{DEMO_BIN} missing (make build-zephyr-demo; needs Zephyr)")
    with open(DEMO_BIN, "rb") as f:
        return f.read()


def test_fits_the_real_board(demo_bin):
    assert len(demo_bin) <= I_RAM_FOR_PROGRAMS


def test_runs_from_i_ram(demo_bin):
    soc = new_soc()
    soc.load_app(demo_bin, "riscv")
    mark = 0
    out = wait_for(soc, b"All Rust application tasks finished successfully!", 40_000_000, start=mark)
    wait_for(soc, b"Entering sleep loop.", 5_000_000, start=mark)
    soc.run(1_000_000)  # into k_sleep(K_FOREVER)
    assert soc.riscv_mode
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out
    assert "[Rust Task] Task iteration 5 completed [OK]" in out
    # k_msleep runs on the machine timer at 18 MHz: 100 ms of mtime, give or take a tick
    # (1 ms at the boards' 1000 ticks/s; at 100 ticks/s it reads 110)
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    assert m, out
    assert 100 <= int(m.group(1)) <= 102, out
