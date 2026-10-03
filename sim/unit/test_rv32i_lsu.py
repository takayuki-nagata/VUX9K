# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""rv32i_lsu: store lane steering and load extraction/extension, against a model."""

import random

import cocotb
from cocotb.triggers import Timer
from unit_models import EDGES32, rand32, sext

# funct3 of loads and stores
LB, LH, LW, LBU, LHU = 0, 1, 2, 4, 5
SB, SH, SW = 0, 1, 2


def store_model(f3, src, low):
    """(store_data, store_we): SB/SH replicate the value on every lane; any other funct3 is SW."""
    if f3 == SB:
        return (src & 0xFF) * 0x0101_0101, 1 << low
    if f3 == SH:
        return (src & 0xFFFF) * 0x0001_0001, 0b1100 if low & 2 else 0b0011
    return src, 0b1111


def load_model(f3, word, sel):
    """load_val: LB/LBU at byte sel, LH/LHU at halfword sel[1]; any other funct3 is LW."""
    byte = (word >> (8 * sel)) & 0xFF
    half = (word >> (16 * (sel >> 1))) & 0xFFFF
    return {LB: sext(byte, 8), LBU: byte, LH: sext(half, 16), LHU: half}.get(f3, word)


async def check(dut, f3, value, low):
    dut.store_f3.value = f3
    dut.store_src.value = value
    dut.addr_low.value = low
    dut.load_f3.value = f3
    dut.load_word.value = value
    dut.load_sel.value = low
    await Timer(1, unit="ns")
    data, we = store_model(f3, value, low)
    ctx = f"funct3={f3} value=0x{value:08x} addr[1:0]={low}"
    assert int(dut.store_data.value) == data, f"store_data, {ctx}"
    assert int(dut.store_we.value) == we, f"store_we, {ctx}"
    assert int(dut.load_val.value) == load_model(f3, value, low), f"load_val, {ctx}"


@cocotb.test()
async def test_lsu_all_widths_and_offsets(dut):
    """Every funct3 (incl. the ones loads/stores don't use) x every byte offset x lane
    patterns that set each byte's sign bit alone"""
    patterns = EDGES32 + (0x0000_0080, 0x0000_8000, 0x0080_0000, 0x8000_0000, 0x7F7F_7F7F, 0x8081_8283)
    for f3 in range(8):
        for low in range(4):
            for value in patterns:
                await check(dut, f3, value, low)


@cocotb.test()
async def test_lsu_random(dut):
    for _ in range(3000):
        await check(dut, random.randrange(8), rand32(), random.randrange(4))
