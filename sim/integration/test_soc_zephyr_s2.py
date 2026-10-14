# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr Rust demo started the way the board starts it, on the gate-level netlist (make
sim-zephyr-demo-gls, test-slow): the first stage boots the real second stage from a FAT32
card, whose `run VUX9K/APPS/ZEPHYR.BIN` copies the demo into I-RAM over the first stage
and soft-resets into it (fw_common::launch's I-RAM RV32 target). The same demo preloaded
into I-RAM is test_soc_zephyr_demo.py (RTL, test-sim); on the emulator both ways are
sim/emu/test_zephyr_demo.py.

Cycles (emulator): the prompt at ~14.4M, the 13 KB load ~27.4M (the second stage runs from
the PSRAM), the demo's end ~5.1M later: ~47M in all, minutes on the netlist.
"""

import re

import cocotb
from soc_env import BANNER, boot_s2, s2_cmd

DONE = b"All Rust application tasks finished successfully!"


# ~47M cycles (2.6 s), the POR not shortened on the netlist; room for 1.5x
@cocotb.test(timeout_time=4000, timeout_unit="ms")
async def test_zephyr_demo_through_the_second_stage(dut):
    """S1 boots S2 from the card, run VUX9K/APPS/ZEPHYR.BIN: Zephyr from I-RAM, k_msleep(100) takes 100 ms"""
    ser, _ = await boot_s2(dut, "ZEPHYR.BIN")
    dut._log.info("[PASS] the first stage booted the second stage from the card")
    out = await s2_cmd(ser, "run VUX9K/APPS/ZEPHYR.BIN", DONE, timeout_cycles=45_000_000)
    assert "I-RAM (RV32), starting at 0x00000000" in out, out
    assert BANNER not in out, out  # the first stage is gone
    assert "Zephyr RTOS Booting on VUX9K SoC!" in out, out
    m = re.search(r"k_msleep\(100\) took (\d+) ms", out)
    # A tick (1 ms at the boards' 1000 ticks/s) plus rounding; 100 ticks/s reads 110
    assert m and 100 <= int(m.group(1)) <= 102, out
    dut._log.info("[PASS] run ZEPHYR.BIN: the demo ran from I-RAM")
