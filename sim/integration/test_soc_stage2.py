# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The real second stage on the RTL (make sim-stage2): the first stage boots it from a FAT32
card laid out as the dist's sd/ (\\VUX9K\\STAGE2.BIN, the demos in \\VUX9K\\APPS\\, make
apps), and its `run` starts a demo on each of fw_common::launch's paths the board uses
first: the hello demo in place in the PSRAM (a jump with a0-a2), the Hack demo in its
segment in the PSRAM (soft reset in Hack mode; at its end it soft-resets back to the first
stage). The other commands and the
refusals are sim/emu/test_stage2.py's; the Zephyr demo started this way runs on the
netlist (test_soc_zephyr_s2.py, make sim-zephyr-demo-gls).

Cycles (the emulator's, which the lockstep holds to the RTL's): the prompt at ~12.8M
(the 35 KB STAGE2.BIN read at ~250 cycles a byte); the second stage, running from the
PSRAM through its caches, loads a demo at ~200-260 cycles a byte.
"""

import re

import cocotb
from soc_env import BANNER, boot_s2, s2_cmd, send_and_wait


def handoff(out: str, tag: str):
    m = re.search(rf"\[{tag}\] a0=0x([0-9A-F]{{8}}) a1=0x([0-9A-F]{{8}}) a2=0x([0-9A-F]{{8}})", out)
    assert m, out
    return m.groups()


# Boot ~12.8M, ls, the hello demo ~1.1M, the soft reset: ~14M cycles (0.8 s); room for 3x
@cocotb.test(timeout_time=2400, timeout_unit="ms")
async def test_s2_ls_and_run_hello_from_the_psram(dut):
    """ls VUX9K/APPS, then run VUX9K/APPS/HELLO.BIN: a0-a2 as the second stage got them, and r back to S1"""
    ser, out = await boot_s2(dut, "HELLO.BIN", "HACK.BIN")
    s2_regs = handoff(out, "S2")
    dut._log.info("[PASS] the first stage booted the second stage from the card")

    out = await s2_cmd(ser, "ls VUX9K/APPS")
    assert re.search(r"\nHELLO\.BIN +\d+\n", out) and re.search(r"\nHACK\.BIN +\d+\n", out), out
    dut._log.info("[PASS] ls VUX9K/APPS")

    out = await s2_cmd(ser, "run VUX9K/APPS/HELLO.BIN", b"for the first stage.\n", timeout_cycles=10_000_000)
    assert '[RUN] v1 "VUX9K hello"' in out and "PSRAM, starting at 0x80000000" in out, out
    assert handoff(out, "HELLO") == s2_regs, out
    dut._log.info("[PASS] run HELLO.BIN: started in the PSRAM with the first stage's a0-a2")

    out = await send_and_wait(ser, "r", BANNER.encode(), timeout_cycles=2_000_000)
    assert "[HELLO] Soft reset" in out, out
    dut._log.info("[PASS] r: soft reset back to the first stage")


# Boot ~12.8M; the load, the data segment zeroed and the demo's first result ~3M, its end
# and its soft reset to the first stage's banner ~2.5M more: ~18.3M cycles (1.0 s); room
# for 4x
@cocotb.test(timeout_time=4100, timeout_unit="ms")
async def test_s2_run_hack_from_its_segment(dut):
    """run VUX9K/APPS/HACK.BIN: loaded into Hack's segment, started in Hack mode, back to the first stage at its end"""
    ser, _ = await boot_s2(dut, "HACK.BIN")
    out = await s2_cmd(ser, "run VUX9K/APPS/HACK.BIN", b"Factorial(6) = 720 ... [PASS]", timeout_cycles=24_000_000)
    assert '[RUN] v1 "Hack demo"' in out and "Hack segment, starting at 0x00000000" in out, out
    assert "VUX9K SoC Hack 16-bit C Firmware Test" in out, out
    assert BANNER not in out, out
    dut._log.info("[PASS] run HACK.BIN: the Hack demo runs from its segment in the PSRAM")

    out = await ser.wait_for(BANNER.encode(), timeout_cycles=8_000_000)
    assert b"ALL HACK C FIRMWARE TESTS PASSED (100%)!" in out, out
    dut._log.info("[PASS] the Hack demo's soft reset: back to the first stage")
