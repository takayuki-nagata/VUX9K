# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
soc_rom (the 16 KB boot ROM at RV32 0x0000_0000) unit tests.

- Two synchronous read ports (one clock): i_addr (the fetch) and d_addr (loads), each
  decoding only bits [13:2], so the 4096 words repeat every 16 KB on both.
- No write port: the contents are the $readmemh preload of firmware.hex (the runner stages
  it into the run directory when build/firmware has it).
The tests put their own words into `mem` through VPI (as soc_env.load_imem does for the
SoC) and read them back through both ports at once.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

WORDS = 4096


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.i_addr.value = 0
    dut.d_addr.value = 0
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def read_both(dut, i_addr, d_addr):
    """Both ports' words one clock after their addresses."""
    dut.i_addr.value = i_addr
    dut.d_addr.value = d_addr
    await FallingEdge(dut.clk)
    return int(dut.i_data_out.value), int(dut.d_data_out.value)


# Skipped, and reported as skipped, when the runner staged no firmware.hex (make firmware;
# test-sim builds it first): without it $readmemh loads nothing
@cocotb.test(skip=not os.path.exists("firmware.hex"))
async def test_preload(dut):
    """The contents at power-up are firmware.hex's words, on both ports"""
    await setup(dut)
    with open("firmware.hex") as f:
        words = [int(w, 16) for w in f.read().split()]
    assert len(words) == WORDS
    for idx in [0, 1, 2, WORDS - 1] + random.sample(range(WORDS), 60):
        got = await read_both(dut, idx << 2, (WORDS - 1 - idx) << 2)
        assert got == (words[idx], words[WORDS - 1 - idx]), f"word {idx}"


@cocotb.test()
async def test_ports_and_aliases(dut):
    """Each port reads the word of its address bits [13:2], whatever the other bits"""
    await setup(dut)
    for idx, value in ((0, 0x0000_0013), (1, 0xDEAD_BEEF), (0x37F, 0x0BAD_F00D), (WORDS - 1, 0x1234_5678)):
        dut.mem[idx].value = value
    await FallingEdge(dut.clk)
    assert await read_both(dut, 0x0, 0x4) == (0x0000_0013, 0xDEAD_BEEF)
    assert await read_both(dut, 0x3FFC, 0x0DFC) == (0x1234_5678, 0x0BAD_F00D)
    # don't-care bits: the byte offset, everything above the 16 KB
    assert await read_both(dut, 0x8000_4007, 0x0000_7FFF) == (0xDEAD_BEEF, 0x1234_5678), "16 KB aliases"
    assert await read_both(dut, 0xFFFF_C001, 0x2000_0002) == (0x0000_0013, 0x0000_0013), "high bits ignored"


@cocotb.test()
async def test_random_both_ports(dut):
    """Random addresses on both ports every cycle, over random contents of a pool of words:
    each port returns the word of the address it had one clock earlier."""
    await setup(dut)
    pool = [0, 1, 2, 0x37F, WORDS - 1] + random.sample(range(3, WORDS - 1), 27)
    mem = {idx: random.getrandbits(32) for idx in pool}
    for idx, value in mem.items():
        dut.mem[idx].value = value
    await FallingEdge(dut.clk)
    for _ in range(3000):
        i_idx, d_idx = random.choice(pool), random.choice(pool)
        i_addr = random.getrandbits(18) << 14 | i_idx << 2 | random.getrandbits(2)
        d_addr = random.getrandbits(18) << 14 | d_idx << 2 | random.getrandbits(2)
        got = await read_both(dut, i_addr, d_addr)
        assert got == (mem[i_idx], mem[d_idx]), f"i_addr=0x{i_addr:08x} d_addr=0x{d_addr:08x}: {got}"
