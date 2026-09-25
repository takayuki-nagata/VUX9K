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
