# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (make build-zephyr-demo, board vux9k) on the emulator's real
profile, started the way the board starts it: the firmware boots from its preload,
the Boot Manager's '1' launches SD slot 1 through the Resident Loader, which loads
the image into I-RAM and soft-resets into it.
"""

import os
import re

import pytest
from vux9k import REPO_ROOT, slot_image, start_soc, wait_for

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")
I_RAM_FOR_PROGRAMS = 0x3800  # below the Resident Loader


@pytest.fixture(scope="module")
def demo_bin():
    if not os.path.exists(DEMO_BIN):
        pytest.skip(f"{DEMO_BIN} missing (make build-zephyr-demo; needs Zephyr)")
    with open(DEMO_BIN, "rb") as f:
        return f.read()


def test_fits_the_real_board(demo_bin):
    assert len(demo_bin) <= I_RAM_FOR_PROGRAMS


def test_boots_from_sd_slot_1(demo_bin):
    slot1, _ = slot_image(demo_bin, slot=1, mode="riscv", name="Zephyr Rust")
    soc = start_soc(sd_sectors=slot1)
    wait_for(soc, b"vux> ", timeout_cycles=3_000_000)
    mark = len(soc.uart_received())
    soc.uart_send(b"1")
    wait_for(soc, b"[RL] Slot 1", timeout_cycles=5_000_000, start=mark)
    out = wait_for(soc, b"All Rust application tasks finished successfully!", 40_000_000, start=mark)
    wait_for(soc, b"Entering sleep loop.", 5_000_000, start=mark)
    soc.run(1_000_000)  # into k_sleep(K_FOREVER)
    assert soc.riscv_mode
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out
    assert "[Rust Task] Task iteration 5 completed [OK]" in out
    # k_msleep runs on the machine timer at 18 MHz: 100 ms of mtime, give or take a tick
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    assert m, out
    assert 100 <= int(m.group(1)) <= 110, out
