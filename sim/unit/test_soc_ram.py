# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
soc_ram (16 KB I-RAM + 8 KB byte-laned D-RAM, Harvard with a data-port view of I-RAM)
unit tests.

- I-RAM: written through i_we/i_waddr (word index), read through i_addr (byte address).
- D-RAM, RISC-V mode (active_mode=1): word index d_addr[12:2], byte enables d_we_byte.
- D-RAM, Hack mode (active_mode=0): word index d_addr[10:0], all byte lanes written.
- RISC-V data reads with d_addr[31:16] == 0 return I-RAM (port B) instead of D-RAM.
All reads are synchronous (one clock). The initial contents come from the firmware
hex files the runner stages into the test directory; tests only rely on what they write.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

DRAM = 0x2000_0000


async def setup(dut, mode=1):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.active_mode.value = mode
    dut.i_addr.value = 0
    dut.i_we.value = 0
    dut.i_waddr.value = 0
    dut.i_wdata.value = 0
    dut.d_addr.value = DRAM
    dut.d_data_in.value = 0
    dut.d_we.value = 0
    dut.d_we_byte.value = 0
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def i_write(dut, word_idx, value):
    await FallingEdge(dut.clk)
    dut.i_waddr.value = word_idx
    dut.i_wdata.value = value
    dut.i_we.value = 1
    await FallingEdge(dut.clk)
    dut.i_we.value = 0


async def i_read(dut, byte_addr):
    await FallingEdge(dut.clk)
    dut.i_addr.value = byte_addr
    await FallingEdge(dut.clk)
    return int(dut.i_data_out.value)


async def d_write(dut, addr, value, byte_en=0xF):
    await FallingEdge(dut.clk)
    dut.d_addr.value = addr
    dut.d_data_in.value = value
    dut.d_we_byte.value = byte_en
    dut.d_we.value = 1
    await FallingEdge(dut.clk)
    dut.d_we.value = 0


async def d_read(dut, addr):
    await FallingEdge(dut.clk)
    dut.d_addr.value = addr
    await FallingEdge(dut.clk)
    return int(dut.d_data_out.value)


@cocotb.test()
async def test_iram_write_read(dut):
    """I-RAM words written by index read back at the matching byte address"""
    await setup(dut)
    for idx, value in ((0, 0x00000013), (1, 0xDEADBEEF), (4095, 0x12345678)):
        await i_write(dut, idx, value)
    assert await i_read(dut, 0x0) == 0x00000013
    assert await i_read(dut, 0x4) == 0xDEADBEEF
    assert await i_read(dut, 0x3FFC) == 0x12345678, "last I-RAM word (16 KB)"


@cocotb.test()
async def test_dram_byte_lanes_riscv(dut):
    """RISC-V mode: each d_we_byte bit writes exactly its lane"""
    await setup(dut, mode=1)
    addr = DRAM + 0x100
    await d_write(dut, addr, 0x11223344)
    assert await d_read(dut, addr) == 0x11223344
    await d_write(dut, addr, 0xAAAAAAAA, byte_en=0b0010)
    assert await d_read(dut, addr) == 0x1122AA44
    await d_write(dut, addr, 0xBBBBBBBB, byte_en=0b1000)
    assert await d_read(dut, addr) == 0xBB22AA44
    await d_write(dut, addr, 0xCCCCCCCC, byte_en=0b0101)
    assert await d_read(dut, addr) == 0xBBCCAACC
    await d_write(dut, addr, 0xFFFFFFFF, byte_en=0)
    assert await d_read(dut, addr) == 0xBBCCAACC, "d_we with no byte enables must not write"


@cocotb.test()
async def test_dram_addressing_riscv(dut):
    """RISC-V mode: D-RAM is 8 KB word-addressed by d_addr[12:2] (aliases every 8 KB)"""
    await setup(dut, mode=1)
    await d_write(dut, DRAM + 0x0, 0x0000A000)
    await d_write(dut, DRAM + 0x4, 0x0000A004)
    await d_write(dut, DRAM + 0x1FFC, 0x0000BFFC)
    assert await d_read(dut, DRAM + 0x0) == 0x0000A000
    assert await d_read(dut, DRAM + 0x4) == 0x0000A004
    assert await d_read(dut, DRAM + 0x1FFC) == 0x0000BFFC
    assert await d_read(dut, DRAM + 0x2004) == 0x0000A004, "D-RAM must alias every 8 KB"


@cocotb.test()
async def test_dram_hack_mode(dut):
    """Hack mode: D-RAM word index is d_addr[10:0] and byte enables are ignored"""
    await setup(dut, mode=0)
    await d_write(dut, 5, 0x0000CAFE, byte_en=0b0001)
    assert await d_read(dut, 5) == 0x0000CAFE, "Hack writes must write all lanes"
    await d_write(dut, 6, 0x00001234, byte_en=0)
    assert await d_read(dut, 6) == 0x00001234
    assert await d_read(dut, 5) == 0x0000CAFE, "adjacent Hack words must be distinct"


@cocotb.test()
async def test_iram_via_data_port(dut):
    """RISC-V reads in the low 64 KB come from I-RAM; Hack mode never does"""
    await setup(dut, mode=1)
    await i_write(dut, 0x40, 0x0BADF00D)
    await d_write(dut, DRAM + 0x100, 0x11111111)
    assert await d_read(dut, 0x100) == 0x0BADF00D, "RISC-V data read of 0x100 must see I-RAM word 0x40"
    assert await d_read(dut, DRAM + 0x100) == 0x11111111, "0x2000_0100 must still read D-RAM"

    dut.active_mode.value = 0
    await d_write(dut, 0x40, 0x00002222)
    assert await d_read(dut, 0x40) == 0x00002222, "Hack mode must read D-RAM, not I-RAM"


@cocotb.test()
async def test_random_both_ports(dut):
    """Random cycles on both ports at once, in both modes, over a small pool of words so
    reads and writes collide: every read returns the word as it was before that edge's
    write, I-RAM through the data port included. Words the test hasn't written yet (they
    hold the staged firmware) aren't checked."""
    await setup(dut, mode=1)
    i_pool = [0, 1, 2, 0x37F, 0xFFF] + random.sample(range(4096), 5)
    d_pool = [0, 1, 0x7FF] + random.sample(range(2048), 5)
    imem, dmem = {}, {}  # word index -> word; D-RAM word index -> [4 bytes or None]

    def d_word(idx):
        lanes = dmem.get(idx)
        return None if lanes is None or None in lanes else sum(b << (8 * i) for i, b in enumerate(lanes))

    for _ in range(3000):
        mode = int(random.random() < 0.8)
        i_widx, i_ridx = random.choice(i_pool), random.choice(i_pool)
        i_we, i_wdata = int(random.random() < 0.3), random.getrandbits(32)
        if mode and random.random() < 0.3:  # a RISC-V data read of I-RAM (low 64 KB, aliased)
            d_addr = random.choice(i_pool) << 2 | random.getrandbits(2) << 14 | random.getrandbits(2)
        elif mode:  # D-RAM, with random don't-care bits above the 8 KB
            d_addr = DRAM | random.getrandbits(15) << 13 | random.choice(d_pool) << 2 | random.getrandbits(2)
        else:  # Hack: a word index; bits above [10:0] don't matter
            d_addr = random.getrandbits(5) << 11 | random.choice(d_pool)
        d_we, d_byte = int(random.random() < 0.3), random.getrandbits(4)
        d_data = random.getrandbits(32)
        dut.active_mode.value = mode
        dut.i_we.value, dut.i_waddr.value, dut.i_wdata.value, dut.i_addr.value = i_we, i_widx, i_wdata, i_ridx << 2
        dut.d_addr.value, dut.d_we.value, dut.d_we_byte.value, dut.d_data_in.value = d_addr, d_we, d_byte, d_data

        d_idx = d_addr >> 2 & 0x7FF if mode else d_addr & 0x7FF
        want_i = imem.get(i_ridx)
        want_d = imem.get(d_addr >> 2 & 0xFFF) if mode and d_addr >> 16 == 0 else d_word(d_idx)
        if i_we:
            imem[i_widx] = i_wdata
        if d_we:
            lanes = dmem.setdefault(d_idx, [None] * 4)
            for b in range(4):
                if not mode or d_byte >> b & 1:
                    lanes[b] = d_data >> (8 * b) & 0xFF
        await FallingEdge(dut.clk)
        ctx = f"mode={mode} i_addr=0x{i_ridx << 2:x} d_addr=0x{d_addr:08x}"
        if want_i is not None:
            assert int(dut.i_data_out.value) == want_i, f"i_data_out, {ctx}"
        if want_d is not None:
            assert int(dut.d_data_out.value) == want_d, f"d_data_out 0x{int(dut.d_data_out.value):08x}, {ctx}"
