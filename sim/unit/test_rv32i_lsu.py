# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""rv32i_lsu: store lane steering and load extraction/extension, against a model."""

import random

import cocotb
import fcov
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


def _loaded_sign(f3, value, low):
    """(f3, offset, sign bit of the loaded byte/halfword) for the sign-extending loads."""
    if f3 == LB:
        return (f3, low, (value >> (8 * low + 7)) & 1)
    if f3 == LH:
        return (f3, low & 2, (value >> (16 * (low >> 1) + 15)) & 1)
    return None


# Every funct3 at every byte offset, and both signs of every byte/halfword LB/LH extend
@fcov.point("lsu.f3", range(8), xf=lambda f3, value, low: f3)
@fcov.point("lsu.offset", range(4), xf=lambda f3, value, low: low)
@fcov.point(
    "lsu.load_sign",
    [(LB, o, s) for o in range(4) for s in (0, 1)] + [(LH, o, s) for o in (0, 2) for s in (0, 1)],
    xf=_loaded_sign,
)
@fcov.cross("lsu.f3_x_offset", ("lsu.f3", "lsu.offset"))
def sample(f3, value, low):
    pass


async def check(dut, f3, value, low):
    sample(f3, value, low)
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
    fcov.export()


@cocotb.test()
async def test_lsu_random(dut):
    for _ in range(3000):
        await check(dut, random.randrange(8), rand32(), random.randrange(4))
    fcov.export()
