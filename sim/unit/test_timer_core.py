# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
timer_core (machine timer: 64-bit mtime / mtimecmp, timer_irq; msip) unit tests.

Two views of the same registers, chosen by `clint`:
- the CLINT view (RV32 region 2, soc_pkg::CLINT_*): addr[15:2] decoded in full, 0x0000
  msip (bit 0), 0x4000/0x4004 mtimecmp lo/hi, 0xBFF8/0xBFFC mtime lo/hi; a byte address
  inside a word hits that word (emu/crates/vux9k_emu/src/periph/clint.rs)
- the TIMER page (0x4000_1000): addr[3:0] 0x0/0x4 mtime lo/hi, 0x8/0xC mtimecmp lo/hi
Any other address reads 0 and ignores writes; msip only through the CLINT view.
Reads are registered: data_out reflects the addressed register at the previous
rising edge. msip is a flop: a write shows on the output after its edge.
"""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from unit_models import rand32

ALL_ONES = 0xFFFFFFFF
MSIP, MTIMECMP, MTIME = 0x0000, 0x4000, 0xBFF8  # soc_pkg::CLINT_*

# The register at a CLINT offset (addr & 0xFFFC) and at a page offset (addr[3:0])
CLINT_REGS = {MSIP: "msip", MTIMECMP: "cmp_lo", MTIMECMP + 4: "cmp_hi", MTIME: "mtime_lo", MTIME + 4: "mtime_hi"}
PAGE_REGS = {0x0: "mtime_lo", 0x4: "mtime_hi", 0x8: "cmp_lo", 0xC: "cmp_hi"}
REGS = ("msip", "mtime_lo", "mtime_hi", "cmp_lo", "cmp_hi", "none")


def decode(clint, addr):
    """The register timer_core's view `clint` selects at addr (None: none)."""
    if clint:
        return CLINT_REGS.get(addr & 0xFFFC)
    return PAGE_REGS.get(addr & 0xF)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.clint.value = 0
    dut.data_in.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst.value = 1


async def write(dut, addr, value, clint=0):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    dut.clint.value = clint
    dut.data_in.value = value
    dut.we.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0


async def read(dut, addr, clint=0):
    """Value of the register at addr as of the rising edge before the returned falling edge."""
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    dut.clint.value = clint
    await FallingEdge(dut.clk)
    return int(dut.data_out.value)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_reset_values(dut):
    """After reset mtimecmp is all-ones, msip is 0 and no interrupt is pending"""
    await setup(dut)
    assert await read(dut, 0x8) == ALL_ONES
    assert await read(dut, 0xC) == ALL_ONES
    assert int(dut.timer_irq.value) == 0
    assert int(dut.msip.value) == 0
    assert await read(dut, 0x4) == 0, "mtime hi must start at 0"
    assert await read(dut, MSIP, clint=1) == 0
    assert await read(dut, MTIMECMP + 4, clint=1) == ALL_ONES


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_counts_every_cycle(dut):
    """mtime advances by exactly one per clock"""
    await setup(dut)
    t0 = await read(dut, 0x0)
    await ClockCycles(dut.clk, 99, rising=False)  # read() itself spans one more edge
    t1 = await read(dut, 0x0)
    assert t1 - t0 == 101, f"mtime advanced {t1 - t0} in 101 cycles"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_write_and_carry(dut):
    """A written low word wins over the increment, and the low word carries into the high word"""
    await setup(dut)
    await write(dut, 0x4, 7)
    await write(dut, 0x0, 0xFFFFFFF0)
    lo = await read(dut, 0x0)
    assert 0xFFFFFFF0 <= lo < 0xFFFFFFF8, f"low word after write: 0x{lo:08X}"
    assert await read(dut, 0x4) == 7
    await ClockCycles(dut.clk, 20)
    assert await read(dut, 0x4) == 8, "low-word overflow must carry into the high word"
    assert await read(dut, 0x0) < 0x40


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtimecmp_readback_and_unmapped(dut):
    """mtimecmp halves read back independently; unmapped offsets read 0 and ignore writes"""
    await setup(dut)
    await write(dut, 0x8, 0x12345678)
    await write(dut, 0xC, 0x9ABCDEF0)
    assert await read(dut, 0x8) == 0x12345678
    assert await read(dut, 0xC) == 0x9ABCDEF0
    await write(dut, 0x2, 0xDEADBEEF)
    for addr in (0x1, 0x2, 0x3, 0x5, 0x9, 0xE):
        assert await read(dut, addr) == 0, f"unmapped offset 0x{addr:X} must read 0"
    assert await read(dut, 0x8) == 0x12345678, "write to an unmapped offset changed mtimecmp"
    # The page view ignores addr[15:4]
    assert await read(dut, 0xFFF8) == 0x12345678


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_timer_irq_compare(dut):
    """timer_irq rises once mtime >= mtimecmp (unsigned 64-bit) and clears when mtimecmp moves up"""
    await setup(dut)
    await write(dut, 0xC, 0)  # hi first: mtimecmp = 0x00000000_FFFFFFFF -> still far away
    now = await read(dut, 0x0)
    target = now + 40
    await write(dut, 0x8, target)
    await ClockCycles(dut.clk, 20)
    assert int(dut.timer_irq.value) == 0, "irq asserted before mtime reached mtimecmp"
    await ClockCycles(dut.clk, 30)
    assert int(dut.timer_irq.value) == 1, "irq not asserted after mtime passed mtimecmp"
    await ClockCycles(dut.clk, 10)
    assert int(dut.timer_irq.value) == 1, "irq must stay asserted while mtime >= mtimecmp"
    await write(dut, 0xC, 1)  # mtimecmp now 2^32 above mtime
    await ClockCycles(dut.clk, 2)
    assert int(dut.timer_irq.value) == 0, "irq must clear after mtimecmp is moved past mtime"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_low_write_does_not_carry(dut):
    """Writing mtime's low word while it is 0xFFFFFFFF leaves the high word alone.

    The write and that edge's +1 land on the same edge: the written low word wins,
    and the increment's carry must not leak into the high word.
    """
    await setup(dut)
    await write(dut, 0x4, 7)
    await write(dut, 0x0, ALL_ONES - 1)  # the next edge (between writes) makes lo 0xFFFFFFFF
    await write(dut, 0x0, 0x100)  # this edge would carry lo 0xFFFFFFFF -> 0 into hi
    assert await read(dut, 0x4) == 7, "low-word write must not carry into the high word"
    lo = await read(dut, 0x0)
    assert 0x100 <= lo < 0x108, f"low word after write: 0x{lo:08X}"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_clint_offsets(dut):
    """The CLINT view: mtimecmp at 0x4000/0x4004, mtime at 0xBFF8/0xBFFC, each also at the
    byte addresses inside its word; the same registers as the page's"""
    await setup(dut)
    await write(dut, MTIMECMP, 0x1122_3344, clint=1)
    await write(dut, MTIMECMP + 4 + 3, 0x5566_7788, clint=1)  # a byte address in the hi word
    assert await read(dut, 0x8) == 0x1122_3344
    assert await read(dut, 0xC) == 0x5566_7788
    assert await read(dut, MTIMECMP + 1, clint=1) == 0x1122_3344
    assert await read(dut, MTIMECMP + 6, clint=1) == 0x5566_7788
    await write(dut, 0x8, 0xA5A5_0000)  # through the page, read through the CLINT
    assert await read(dut, MTIMECMP + 2, clint=1) == 0xA5A5_0000
    await write(dut, MTIME + 4, 9, clint=1)
    await write(dut, MTIME + 2, 0x100, clint=1)
    lo = await read(dut, MTIME, clint=1)
    assert 0x100 <= lo < 0x108, f"mtime lo through the CLINT: 0x{lo:08X}"
    assert await read(dut, MTIME + 4, clint=1) == 9
    assert await read(dut, 0x4) == 9
    page_lo = await read(dut, 0x0)
    assert page_lo == lo + 6, "both views read the same mtime (three reads, two edges each, later)"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_clint_undefined_offsets(dut):
    """Every CLINT offset that is no register reads 0 and ignores writes; the page's offsets
    (0x0-0xF) are not the page's registers in the CLINT view"""
    await setup(dut)
    await write(dut, MTIMECMP, 0x1234_5678, clint=1)
    for off in (0x0004, 0x0008, 0x000C, 0x0010, 0x1000, 0x3FFC, 0x4008, 0x8000, 0xBFF0, 0xBFF4, 0xC000, 0xFFFC):
        await write(dut, off, ALL_ONES, clint=1)
        assert await read(dut, off, clint=1) == 0, f"CLINT offset 0x{off:04X} must read 0"
    assert await read(dut, MTIMECMP, clint=1) == 0x1234_5678
    assert await read(dut, MTIMECMP + 4, clint=1) == ALL_ONES
    assert await read(dut, MSIP, clint=1) == 0
    assert int(dut.msip.value) == 0
    assert await read(dut, MTIME + 4, clint=1) == 0, "mtime hi untouched"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_msip(dut):
    """msip: bit 0 of the written word (any byte address of word 0), read back alone, on the
    output from the edge after the write; the page view can't reach it"""
    await setup(dut)
    await FallingEdge(dut.clk)
    dut.addr.value, dut.clint.value, dut.data_in.value, dut.we.value = MSIP, 1, ALL_ONES, 1
    await FallingEdge(dut.clk)  # the write's edge has passed
    dut.we.value = 0
    assert int(dut.msip.value) == 1, "msip shows the write after its edge"
    assert await read(dut, MSIP, clint=1) == 1, "only bit 0 reads back"
    await write(dut, MSIP + 2, 0xFFFF_FFFE, clint=1)  # bit 0 clear, at a byte address
    assert int(dut.msip.value) == 0
    await write(dut, MSIP + 3, 1, clint=1)
    assert int(dut.msip.value) == 1
    await write(dut, 0x0, 0, clint=0)  # the page's offset 0 is mtime lo, not msip
    assert int(dut.msip.value) == 1
    assert await read(dut, MSIP, clint=1) == 1
    await write(dut, MSIP, 0, clint=1)
    assert int(dut.msip.value) == 0
    assert await read(dut, MSIP, clint=1) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtip_through_the_clint(dut):
    """timer_irq from mtimecmp written through the CLINT view, cleared through it again"""
    await setup(dut)
    now = await read(dut, MTIME, clint=1)
    await write(dut, MTIMECMP, now + 30, clint=1)
    await write(dut, MTIMECMP + 4, 0, clint=1)
    assert int(dut.timer_irq.value) == 0
    await ClockCycles(dut.clk, 40)
    assert int(dut.timer_irq.value) == 1
    await write(dut, MTIMECMP + 4, ALL_ONES, clint=1)
    await ClockCycles(dut.clk, 2)
    assert int(dut.timer_irq.value) == 0


class TimerModel:
    """timer_core per rising edge: reads and timer_irq show the registers before the edge,
    msip the flop after it; mtime counts every edge, and a write to a half replaces that
    half (a low write also drops that edge's +1, a high write keeps the low half's +1 but
    not its carry)."""

    def __init__(self, mtime, mtimecmp, msip=0):
        self.mtime, self.mtimecmp, self.msip = mtime, mtimecmp, msip

    def edge(self, we, reg, data):
        m, c, s = self.mtime, self.mtimecmp, self.msip
        out = {
            "msip": s,
            "mtime_lo": m & ALL_ONES,
            "mtime_hi": m >> 32,
            "cmp_lo": c & ALL_ONES,
            "cmp_hi": c >> 32,
        }.get(reg, 0)
        irq = int(m >= c)
        m_next = (m + 1) & (1 << 64) - 1
        if we and reg == "msip":
            s = data & 1
        elif we and reg == "mtime_lo":
            m_next = m & ~ALL_ONES | data
        elif we and reg == "mtime_hi":
            m_next = data << 32 | m_next & ALL_ONES
        elif we and reg == "cmp_lo":
            c = c & ~ALL_ONES | data
        elif we and reg == "cmp_hi":
            c = data << 32 | c & ALL_ONES
        self.mtime, self.mtimecmp, self.msip = m_next, c, s
        return out, irq, s


# The CLINT view's cases: each register (or none) read and written; msip set and cleared;
# timer_irq rising after an mtime/mtimecmp write through the CLINT (the view of the last one)
@fcov.point(
    "clint.access",
    [(r, w) for r in REGS for w in (0, 1)],
    xf=lambda clint, reg, we, msip_change, mtip_rise: (reg or "none", we) if clint else None,
)
@fcov.point("clint.msip", ("set", "clear"), xf=lambda clint, reg, we, msip_change, mtip_rise: msip_change)
@fcov.point("clint.mtip_rise", ("clint",), xf=lambda clint, reg, we, msip_change, mtip_rise: mtip_rise)
def sample(clint, reg, we, msip_change, mtip_rise):
    pass


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_random_against_model(dut):
    """Random reads and writes in both views (every register, byte addresses inside them,
    unmapped offsets, the page's ignored upper bits), with values aimed at the low word's
    carry and at mtimecmp just around mtime, checked every cycle. Starts with a directed
    MTIP rise through the CLINT view, so the fcov bin doesn't depend on the seed."""
    await setup(dut)
    # Known state: write mtime hi, then lo (the lo write drops that edge's increment)
    await write(dut, 0x4, 0)
    await write(dut, 0x0, 0)
    await write(dut, 0x8, ALL_ONES)
    await write(dut, 0xC, ALL_ONES)
    # write() waits for a falling edge, writes on the next rising one and returns at the
    # falling edge after it: each mtimecmp write takes two edges, so mtime is 4 by now
    model = TimerModel(4, (1 << 64) - 1)

    directed = [(1, 1, MTIMECMP, 40), (1, 1, MTIMECMP + 4, 0)] + [(0, 1, MTIME, 0)] * 40
    directed += [(1, 1, MTIMECMP + 4, ALL_ONES), (1, 1, MSIP, 1), (1, 1, MSIP, 0)]
    # Every CLINT register (and an unmapped offset) read and written at least once
    directed += [(0, 1, a, 0) for a in (*CLINT_REGS, 0x8000)]
    directed += [(1, 1, MTIME + 4, 0), (1, 1, MTIME, 100), (1, 1, 0x8000, ALL_ONES)]
    last_view = 0
    for k in range(len(directed) + 4000):
        if k < len(directed):
            we, clint, addr, data = directed[k]
        else:
            clint = random.random() < 0.5
            r = random.random()
            if clint:
                if r < 0.85:
                    addr = random.choice(list(CLINT_REGS)) | random.randrange(4)
                else:
                    addr = random.getrandbits(16)
            else:
                addr = random.choice((0x0, 0x4, 0x8, 0xC)) if r < 0.85 else random.randrange(16)
                addr |= random.getrandbits(12) << 4  # ignored by the page view
            we = random.random() < 0.3
            m = model.mtime
            if random.random() < 0.6:  # aim near the interesting values
                data = (
                    random.choice(
                        (
                            (m & ALL_ONES) + random.randint(-3, 3),
                            (m >> 32) + random.randint(-1, 1),
                            ALL_ONES - random.randint(0, 6),
                            random.randint(0, 3),
                        )
                    )
                    & ALL_ONES
                )
            else:
                data = rand32()
        reg = decode(clint, addr)
        dut.we.value, dut.clint.value, dut.addr.value, dut.data_in.value = int(we), int(clint), addr, data
        old_msip, old_irq = model.msip, int(model.mtime >= model.mtimecmp)
        out, irq, msip = model.edge(we, reg, data)
        if we and reg in ("mtime_lo", "mtime_hi", "cmp_lo", "cmp_hi"):
            last_view = int(clint)
        await FallingEdge(dut.clk)
        ctx = f"we={int(we)} clint={int(clint)} addr=0x{addr:04x} data=0x{data:08x}"
        assert int(dut.data_out.value) == out, f"data_out 0x{int(dut.data_out.value):08x} != 0x{out:08x}, {ctx}"
        assert int(dut.timer_irq.value) == irq, f"timer_irq, {ctx}"
        assert int(dut.msip.value) == msip, f"msip, {ctx}"
        new_irq = int(model.mtime >= model.mtimecmp)  # timer_irq after the next edge
        msip_change = {(0, 1): "set", (1, 0): "clear"}.get((old_msip, msip))
        sample(int(clint), reg, int(we), msip_change, "clint" if last_view and new_irq > old_irq else None)
    fcov.export()
