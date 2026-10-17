# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo started the way the board starts it, on the gate-level netlist (make
sim-zephyr-demo-gls, test-slow): the first stage boots the real second stage from a FAT32
card, whose `run VUX9K/APPS/ZEPHYR.BIN` loads the demo into the PSRAM, where the board vux9k
links it, and jumps to it (fw_common::launch's PSRAM target), the first stage staying in
the ROM. The same demo preloaded into the PSRAM is test_soc_zephyr_demo.py (RTL, test-sim); on
the emulator both ways are sim/emu/test_zephyr_demo.py.

Cycles (emulator): the prompt at ~12.8M, the 13 KB load ~3.5M (the second stage runs from
the PSRAM, through its caches), then the demo: minutes on the netlist.
"""

import re

import cocotb
from soc_env import BANNER, boot_s2, s2_cmd

DONE = b"All Rust application tasks finished successfully!"


# The POR not shortened on the netlist; the waits allow 20M + 60M cycles (4.4 s)
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
    dut._log.info(f"k_msleep(100) took {m.group(1)} ms")
    # A tick (1 ms at the boards' 1000 ticks/s) plus rounding, as from I-RAM (from the
    # uncached PSRAM it read 103-104); 100 ticks/s reads 110 or more
    assert 100 <= int(m.group(1)) <= 102, out
    dut._log.info("[PASS] run ZEPHYR.BIN: the demo ran from the PSRAM")
