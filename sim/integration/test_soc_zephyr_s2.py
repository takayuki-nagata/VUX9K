# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo started the way the board starts it, on the gate-level netlist (make
sim-zephyr-demo-gls, test-slow): the first stage boots the real second stage from a FAT32
card, whose `run VUX9K/APPS/ZEPHYR.BIN` loads the demo into the PSRAM, where the board vux9k
links it, and jumps to it (fw_common::launch's PSRAM target), the first stage staying in
I-RAM. The same demo preloaded into the PSRAM is test_soc_zephyr_demo.py (RTL, test-sim); on
the emulator both ways are sim/emu/test_zephyr_demo.py.

Cycles (emulator, I-RAM build): the prompt at ~14.4M, the 13 KB load ~27.4M (the second
stage runs from the PSRAM); the demo itself, now from the PSRAM too, an estimated ~5M more
(to be measured): ~47M in all, minutes on the netlist.
"""

import re

import cocotb
from soc_env import BANNER, boot_s2, s2_cmd

DONE = b"All Rust application tasks finished successfully!"
# What the demo measured k_msleep(100) to take, for test_k_msleep_within_a_tick
_k_msleep_ms: list[int] = []


# ~47M cycles (2.6 s), the POR not shortened on the netlist; the waits allow 20M + 60M (4.4 s)
@cocotb.test(timeout_time=5000, timeout_unit="ms")
async def test_zephyr_demo_through_the_second_stage(dut):
    """S1 boots S2 from the card, run VUX9K/APPS/ZEPHYR.BIN: Zephyr from the PSRAM, k_msleep(100) takes 100 ms"""
    ser, _ = await boot_s2(dut, "ZEPHYR.BIN")
    dut._log.info("[PASS] the first stage booted the second stage from the card")
    out = await s2_cmd(ser, "run VUX9K/APPS/ZEPHYR.BIN", DONE, timeout_cycles=60_000_000)
    assert "PSRAM, starting at 0x80000000" in out, out
    assert BANNER not in out, out  # nothing restarts the boot
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out, out
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    assert m, out
    _k_msleep_ms.append(int(m.group(1)))
    dut._log.info(f"k_msleep(100) took {m.group(1)} ms")
    # A tick (1 ms at the boards' 1000 ticks/s) plus rounding and the kernel's own time from
    # the uncached PSRAM (it read 103 here); 100 ticks/s reads 110 or more
    assert 100 <= int(m.group(1)) <= 105, out
    dut._log.info("[PASS] run ZEPHYR.BIN: the demo ran from the PSRAM")


# Strict: a pass fails the run. The measurement of the test above, no simulation of its own.
@cocotb.test(expect_fail=True)
async def test_k_msleep_within_a_tick(dut):
    """k_msleep(100) to 102 ms, as from I-RAM

    Code runs from the uncached PSRAM; the cache (B4a) should bring it back."""
    if not _k_msleep_ms:  # not an assertion: expect_fail would take it for the failure
        raise RuntimeError("test_zephyr_demo_through_the_second_stage measured no k_msleep(100)")
    assert _k_msleep_ms[0] <= 102, _k_msleep_ms[0]
