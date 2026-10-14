# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The real second stage on the RTL (make sim-stage2): the first stage boots it from a FAT32
card laid out as the dist's sd/ (\\VUX9K\\STAGE2.BIN, the demos in \\VUX9K\\APPS\\, make
apps), and its `run` starts a demo on each of fw_common::launch's paths the board uses
first: the hello demo in place in the PSRAM (a jump with a0-a2), the Hack demo in I-RAM
(copied over the first stage, soft reset in Hack mode). The other commands and the
refusals are sim/emu/test_stage2.py's; the Zephyr demo started this way runs on the
netlist (test_soc_zephyr_s2.py, make sim-zephyr-demo-gls).

Cycles (the emulator's, which the lockstep holds to the RTL's): the prompt at ~14.4M
(the 35 KB STAGE2.BIN read at ~250 cycles a byte), `run HELLO.BIN` to its last line
~5.8M, `run HACK.BIN` to the demo's first result ~15.1M (the second stage, running from
the PSRAM, loads 5.5 KB at ~2,400 cycles a byte).
"""

import re

import cocotb
from soc_env import BANNER, boot_s2, s2_cmd, send_and_wait


def handoff(out: str, tag: str):
    m = re.search(rf"\[{tag}\] a0=0x([0-9A-F]{{8}}) a1=0x([0-9A-F]{{8}}) a2=0x([0-9A-F]{{8}})", out)
    assert m, out
    return m.groups()


# Boot ~14.4M, ls, the hello demo ~5.8M, the soft reset: ~21M cycles (1.2 s); room for 2x
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


# Boot ~14.4M, the load ~13.4M, the demo's first result ~1.7M: ~30M cycles (1.7 s); room for 2x
@cocotb.test(timeout_time=3400, timeout_unit="ms")
async def test_s2_run_hack_from_i_ram(dut):
    """run VUX9K/APPS/HACK.BIN: copied into I-RAM over the first stage, started in Hack mode"""
    ser, _ = await boot_s2(dut, "HACK.BIN")
    out = await s2_cmd(ser, "run VUX9K/APPS/HACK.BIN", b"Factorial(6) = 720 ... [PASS]", timeout_cycles=24_000_000)
    assert '[RUN] v1 "Hack demo"' in out and "I-RAM (Hack), starting at 0x00000000" in out, out
    assert "VUX9K SoC Hack 16-bit C Firmware Test" in out, out
    assert BANNER not in out, out  # the first stage is gone
    dut._log.info("[PASS] run HACK.BIN: the Hack demo runs from I-RAM")
