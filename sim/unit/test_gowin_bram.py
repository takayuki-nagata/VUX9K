# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SP, SDPB, DPX9B and SDPX9B block-RAM models (sim/gowin_cells_sim.veryl) through
sim/tb_gowin_bram.veryl.

No GLS netlist instantiates SP or SDPB, so this is their only test; the x9 ones are
used by the caches' netlists, but without INIT_RAM_* contents. Addresses follow
the Gowin convention the models implement: AD is a bit address whose low
log2(width) bits are ignored, i.e. word n of a x32 port is AD = n << 5.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


def word_ad(word: int, width: int) -> int:
    return word << {1: 0, 2: 1, 4: 2, 8: 3, 9: 3, 16: 4, 18: 4, 32: 5, 36: 5}[width]


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.sp_ad.value = 0
    dut.sdpb_we.value = 0
    dut.sdpb_ada.value = 0
    dut.sdpb_di.value = 0
    dut.sdpb_adb.value = 0
    for p in ("dpx9b", "sdpx9b"):
        for sig in ("we", "ada", "di", "adb"):
            getattr(dut, f"{p}_{sig}").value = 0
    await ClockCycles(dut.clk, 2)


async def read(dut, ad_sig, ad: int, do_sig) -> int:
    ad_sig.value = ad
    await RisingEdge(dut.clk)  # synchronous read: DO updates on this edge
    await ReadOnly()
    value = int(do_sig.value)
    await RisingEdge(dut.clk)
    return value


@cocotb.test()
async def test_sp_init_blocks(dut):
    """Each INIT_RAM_xx fills bits [xx*256 +: 256]; block 0x1D used to land at 29*288."""
    await setup(dut)
    for block, low_word in ((0x1C, 0xC01DC01C), (0x1D, 0xC01DC01D), (0x1E, 0xC01DC01E)):
        word = block * 256 // 32
        got = await read(dut, dut.sp_ad, word_ad(word, 32), dut.sp_do)
        assert got == low_word, f"SP INIT_RAM_{block:02X}: word {word} = {got:#010x}, expected {low_word:#010x}"
        got = await read(dut, dut.sp_ad, word_ad(word + 1, 32), dut.sp_do)
        expected = (block * 0x01010101) & 0xFFFFFFFF
        assert got == expected, f"SP INIT_RAM_{block:02X}: word {word + 1} = {got:#010x}, expected {expected:#010x}"


@cocotb.test()
async def test_sdpb_init_and_widths(dut):
    """SDPB loads INIT_RAM_* and addresses each port by its own width (x16 write, x32 read)."""
    await setup(dut)
    word32 = 0x05 * 256 // 32
    got = await read(dut, dut.sdpb_adb, word_ad(word32, 32), dut.sdpb_do)
    assert got == 0x5EED0005, f"SDPB INIT_RAM_05 word {word32} = {got:#010x}"

    # Overwrite the upper halfword of that 32-bit word through the x16 port
    dut.sdpb_ada.value = word_ad(2 * word32 + 1, 16)
    dut.sdpb_di.value = 0x0000BEEF
    dut.sdpb_we.value = 1
    await RisingEdge(dut.clk)
    dut.sdpb_we.value = 0
    got = await read(dut, dut.sdpb_adb, word_ad(word32, 32), dut.sdpb_do)
    assert got == 0xBEEF0005, f"SDPB after x16 write: {got:#010x}, expected 0xbeef0005"


# The tb's 288-bit INIT_RAM_* blocks: a filler byte, the low word distinct
DPX9B_INIT_07 = int("07" * 36, 16) >> 18 << 18 | 0x2D007
SDPX9B_INIT_2A = int("2a" * 36, 16) >> 36 << 36 | 0x9_B00B_002A


async def write(dut, prefix: str, ad: int, value: int):
    getattr(dut, f"{prefix}_ada").value = ad
    getattr(dut, f"{prefix}_di").value = value
    getattr(dut, f"{prefix}_we").value = 1
    await RisingEdge(dut.clk)
    getattr(dut, f"{prefix}_we").value = 0


@cocotb.test()
async def test_dpx9b_init_and_write(dut):
    """DPX9B: INIT_RAM_xx fills bits [xx*288 +: 288] (16 x18 words a block); a port A write
    is read back through port B."""
    await setup(dut)
    word = 0x07 * 288 // 18
    for i in range(2):
        expected = DPX9B_INIT_07 >> (18 * i) & 0x3FFFF
        got = await read(dut, dut.dpx9b_adb, word_ad(word + i, 18), dut.dpx9b_do)
        assert got == expected, f"DPX9B INIT_RAM_07 word {word + i} = {got:#07x}, expected {expected:#07x}"
    await write(dut, "dpx9b", word_ad(word, 18), 0x3_BEEF)
    got = await read(dut, dut.dpx9b_adb, word_ad(word, 18), dut.dpx9b_do)
    assert got == 0x3_BEEF, f"DPX9B after write: {got:#07x}"
    got = await read(dut, dut.dpx9b_adb, word_ad(word + 1, 18), dut.dpx9b_do)
    assert got == DPX9B_INIT_07 >> 18 & 0x3FFFF, f"DPX9B write spilled into the next word: {got:#07x}"


@cocotb.test()
async def test_sdpx9b_init_and_widths(dut):
    """SDPX9B loads INIT_RAM_* in 288-bit blocks and addresses each port by its own width
    (x18 write, x36 read)."""
    await setup(dut)
    word36 = 0x2A * 288 // 36
    got = await read(dut, dut.sdpx9b_adb, word_ad(word36, 36), dut.sdpx9b_do)
    assert got == 0x9_B00B_002A, f"SDPX9B INIT_RAM_2A word {word36} = {got:#011x}"
    await write(dut, "sdpx9b", word_ad(2 * word36 + 1, 18), 0x2_BEEF)
    got = await read(dut, dut.sdpx9b_adb, word_ad(word36, 36), dut.sdpx9b_do)
    expected = 0x2_BEEF << 18 | 0x9_B00B_002A & 0x3FFFF
    assert got == expected, f"SDPX9B after x18 write: {got:#011x}, expected {expected:#011x}"
