# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_addr_decoder: the data-address map of README ("Memory & MMIO Register Map", "Address
decoding and aliases"), in RV32 and Hack mode, against a model; at most one select is ever
active."""

import random

import cocotb
from cocotb.triggers import Timer
from unit_models import rand32

SELECTS = ("ram", "uart", "timer", "sd", "gpio")


def model(addr, hack):
    """The select that addr hits (None: unmapped). Regions decode only the bits they
    need, so they repeat (README, aliasing)."""
    if hack:
        a = addr & 0xFFFF
        if a >> 4 == 0x600:
            return "uart" if (a >> 2) & 3 == 0 else "gpio"
        return "ram" if a < 0x6000 else None
    region, page = addr >> 28, (addr >> 12) & 0xF
    if region in (0x0, 0x2):
        return "ram"
    if region == 0x4:
        return {0: "uart", 1: "timer", 2: "sd", 3: "gpio"}.get(page)
    return None


async def check(dut, addr, hack):
    dut.addr.value = addr
    dut.hack_mode.value = hack
    await Timer(1, unit="ns")
    got = [s for s in SELECTS if int(getattr(dut, s).value)]
    want = model(addr, hack)
    assert got == ([want] if want else []), f"{'Hack' if hack else 'RV32'} 0x{addr:08x}: {got}, want {want}"


@cocotb.test()
async def test_rv32_regions_and_pages(dut):
    """Each region x each MMIO page, at the start and end of the page and with the
    don't-care bits set"""
    for region in range(16):
        for page in range(16):
            for low in (0x000, 0x004, 0xFFC, 0xFFF):
                for mid in (0x0000, 0x0FFF_0000):
                    await check(dut, region << 28 | mid | page << 12 | low, 0)


@cocotb.test()
async def test_hack_map(dut):
    """RAM below 0x6000, the MMIO block's UART/GPIO words, nothing above; upper bits ignored"""
    for a in (0x0000, 0x1FFF, 0x5FFF, 0x6000, 0x6003, 0x6004, 0x600F, 0x6010, 0x7FFF, 0xFFFF):
        for high in (0, 0xABCD_0000):
            await check(dut, high | a, 1)


@cocotb.test()
async def test_random(dut):
    for _ in range(5000):
        hack = random.random() < 0.3
        addr = rand32()
        if random.random() < 0.5:  # aim at the mapped areas
            addr = (random.choice((0x0, 0x2, 0x4)) << 28 | addr & 0x0FFF_FFFF) if not hack else 0x6000 | addr & 0xF
        await check(dut, addr, hack)
