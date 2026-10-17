# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo (make build-zephyr-demo, board vux9k) on the emulator's real
profile, started two ways with the same expectations: loaded into the PSRAM as the image of
an application (`vux9k-emu --load ... --mode riscv`, at 0x8000_0000 by default), and as the
board starts it, from the card: the first stage boots the second, whose `run
VUX9K/APPS/ZEPHYR.BIN` (make apps) loads it into the PSRAM and jumps to it, the first stage
staying in the ROM.
"""

import os
import re

import pytest
from s1_env import STAGE1_DIR, STAGE2_BASE, run_from_card
from vux9k import REPO_ROOT, XMEM_BASE, new_soc, read_hex_words, wait_for

DEMO_BIN = os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin")
# The board's flash (vux9k.dts): 4 MB of the PSRAM from XMEM_BASE; the second stage's
# `run` takes an image up to its own window
FLASH_BYTES = 4 * 1024 * 1024
# From the PSRAM, through its caches, the demo ends ~3M cycles after its start; room for
# several times that
DONE_CYCLES = 40_000_000


@pytest.fixture(scope="module")
def demo_bin():
    if not os.path.exists(DEMO_BIN):
        pytest.skip(f"{DEMO_BIN} missing (make build-zephyr-demo; needs Zephyr)")
    with open(DEMO_BIN, "rb") as f:
        return f.read()


def start(how: str, demo_bin: bytes):
    """The SoC running the demo, and where its output starts in uart_received()."""
    if how == "load_app":
        soc = new_soc()
        soc.load_app(demo_bin, "riscv", XMEM_BASE)
        return soc, 0
    soc, mark = run_from_card("ZEPHYR.BIN")
    # The card's file is the demo as built, in the PSRAM; the first stage is still in the ROM
    assert soc.xmem_word(XMEM_BASE) == int.from_bytes(demo_bin[:4], "little")
    assert all(soc.rom_word(4 * i) == w for i, w in enumerate(read_hex_words(f"{STAGE1_DIR}/firmware.hex")))
    return soc, mark


def test_fits_the_board_and_the_run_window(demo_bin):
    assert len(demo_bin) <= min(FLASH_BYTES, STAGE2_BASE - XMEM_BASE)


_runs: dict[str, tuple[str, bool, int]] = {}


def run_demo(demo_bin: bytes, how: str) -> tuple[str, bool, int]:
    """The demo's output, and the mode and pc in k_sleep(K_FOREVER) at its end (run once
    per `how`)."""
    if how not in _runs:
        soc, mark = start(how, demo_bin)
        out = wait_for(soc, b"All Rust application tasks finished successfully!", DONE_CYCLES, start=mark)
        wait_for(soc, b"Entering sleep loop.", 5_000_000, start=mark)
        soc.run(1_000_000)  # into k_sleep(K_FOREVER)
        _runs[how] = (out, soc.riscv_mode, soc.pc)
    return _runs[how]


def k_msleep_100(out: str) -> int:
    """What the demo measured k_msleep(100) to take, in ms (not an assertion when the line
    is missing: the expected failure below must not swallow that)."""
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    if not m:
        raise ValueError(f"no k_msleep(100) line in {out!r}")
    return int(m.group(1))


@pytest.mark.parametrize("how", ["load_app", "s2_run"])
def test_runs_from_the_psram(demo_bin, how):
    out, riscv_mode, pc = run_demo(demo_bin, how)
    assert riscv_mode
    assert XMEM_BASE <= pc < XMEM_BASE + FLASH_BYTES, hex(pc)
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out
    assert "[Rust Task] Task iteration 5 completed [OK]" in out
    assert "VUX9K Stage" not in out  # nothing of the first or second stage after the start
    # k_msleep runs on the machine timer at 18 MHz: 100 ms of mtime, plus a tick (1 ms at
    # the boards' 1000 ticks/s) and rounding, as from I-RAM (from the uncached PSRAM it read
    # 104); at 100 ticks/s it reads 110 or more
    assert 100 <= k_msleep_100(out) <= 102, out
