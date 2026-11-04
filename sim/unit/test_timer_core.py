# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
timer_core (machine timer: 64-bit mtime / mtimecmp, timer_irq; msip) unit tests.

The CLINT (RV32 region 2, soc_pkg::CLINT_*): addr[15:2] decoded in full, 0x0000 msip
(bit 0), 0x4000/0x4004 mtimecmp lo/hi, 0xBFF8/0xBFFC mtime lo/hi; a byte address inside a
word hits that word (emu/crates/vux9k_emu/src/periph/clint.rs). Any other offset reads 0
and ignores writes.
Reads are registered: data_out reflects the addressed register at the previous
rising edge. msip is a flop: a write shows on the output after its edge.
"""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from unit_models import rand32

ALL_ONES = 0xFFFFFFFF
MSIP, MTIMECMP, MTIME = 0x0000, 0x4000, 0xBFF8  # soc_pkg::CLINT_*

MTIME_HI, MTIMECMP_HI = MTIME + 4, MTIMECMP + 4

# The register at a CLINT offset (addr & 0xFFFC)
CLINT_REGS = {MSIP: "msip", MTIMECMP: "cmp_lo", MTIMECMP_HI: "cmp_hi", MTIME: "mtime_lo", MTIME_HI: "mtime_hi"}
REGS = ("msip", "mtime_lo", "mtime_hi", "cmp_lo", "cmp_hi", "none")


def decode(addr):
    """The register timer_core selects at addr (None: none)."""
    return CLINT_REGS.get(addr & 0xFFFC)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.data_in.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst.value = 1


async def write(dut, addr, value):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    dut.data_in.value = value
    dut.we.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0


async def read(dut, addr):
    """Value of the register at addr as of the rising edge before the returned falling edge."""
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    await FallingEdge(dut.clk)
    return int(dut.data_out.value)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_reset_values(dut):
    """After reset mtimecmp is all-ones, msip is 0 and no interrupt is pending"""
    await setup(dut)
    assert await read(dut, MTIMECMP) == ALL_ONES
    assert await read(dut, MTIMECMP_HI) == ALL_ONES
    assert int(dut.timer_irq.value) == 0
    assert int(dut.msip.value) == 0
    assert await read(dut, MTIME_HI) == 0, "mtime hi must start at 0"
    assert await read(dut, MSIP) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_reset_mid_run(dut):
    """A reset after every flop has left its reset value brings each back, at once (it is
    asynchronous) and without counting while held"""
    await setup(dut)
    await write(dut, MSIP, 1)
    await write(dut, MTIME_HI, 5)
    await write(dut, MTIMECMP, 0)
    await write(dut, MTIMECMP_HI, 0)
    dut.addr.value = MSIP
    await ClockCycles(dut.clk, 2, rising=False)
    assert (int(dut.msip.value), int(dut.timer_irq.value), int(dut.data_out.value)) == (1, 1, 1)
    dut.rst.value = 0
    await Timer(1, unit="ns")  # before the next rising edge
    assert (int(dut.msip.value), int(dut.timer_irq.value), int(dut.data_out.value)) == (0, 0, 0)
    await ClockCycles(dut.clk, 3, rising=False)
    assert (int(dut.msip.value), int(dut.timer_irq.value), int(dut.data_out.value)) == (0, 0, 0)
    dut.rst.value = 1
    assert await read(dut, MTIME_HI) == 0
    assert await read(dut, MTIME) < 8, "mtime restarts from 0 at the release"
    assert await read(dut, MTIMECMP) == ALL_ONES
    assert await read(dut, MTIMECMP_HI) == ALL_ONES


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_every_address_bit_is_decoded(dut):
    """Each register's offset with one bit of addr[15:2] flipped, where that is no register
    (msip and mtimecmp are bit 14 apart, the halves bit 2), reads 0, and a write there
    changes nothing"""
    await setup(dut)
    await write(dut, MSIP, 1)
    await write(dut, MTIMECMP, 0x1234_5678)
    await write(dut, MTIMECMP_HI, 0x9ABC_DEF0)
    for base in CLINT_REGS:
        for bit in range(2, 16):
            off = base ^ (1 << bit)
            if decode(off) is not None:
                continue
            assert await read(dut, off) == 0, f"0x{off:04X} (0x{base:04X} with bit {bit} flipped)"
            await write(dut, off, 0)
    assert int(dut.msip.value) == 1
    assert await read(dut, MTIMECMP) == 0x1234_5678
    assert await read(dut, MTIMECMP_HI) == 0x9ABC_DEF0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_counts_every_cycle(dut):
    """mtime advances by exactly one per clock"""
    await setup(dut)
    t0 = await read(dut, MTIME)
    await ClockCycles(dut.clk, 99, rising=False)  # read() itself spans one more edge
    t1 = await read(dut, MTIME)
    assert t1 - t0 == 101, f"mtime advanced {t1 - t0} in 101 cycles"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_write_and_carry(dut):
    """A written low word wins over the increment, and the low word carries into the high word"""
    await setup(dut)
    await write(dut, MTIME_HI, 7)
    await write(dut, MTIME, 0xFFFFFFF0)
    lo = await read(dut, MTIME)
    assert 0xFFFFFFF0 <= lo < 0xFFFFFFF8, f"low word after write: 0x{lo:08X}"
    assert await read(dut, MTIME_HI) == 7
    await ClockCycles(dut.clk, 20)
    assert await read(dut, MTIME_HI) == 8, "low-word overflow must carry into the high word"
    assert await read(dut, MTIME) < 0x40


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtimecmp_readback(dut):
    """mtimecmp halves read back independently"""
    await setup(dut)
    await write(dut, MTIMECMP, 0x12345678)
    await write(dut, MTIMECMP_HI, 0x9ABCDEF0)
    assert await read(dut, MTIMECMP) == 0x12345678
    assert await read(dut, MTIMECMP_HI) == 0x9ABCDEF0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_timer_irq_compare(dut):
    """timer_irq rises once mtime >= mtimecmp (unsigned 64-bit) and clears when mtimecmp moves up"""
    await setup(dut)
    await write(dut, MTIMECMP_HI, 0)  # hi first: mtimecmp = 0x00000000_FFFFFFFF -> still far away
    now = await read(dut, MTIME)
    target = now + 40
    await write(dut, MTIMECMP, target)
    await ClockCycles(dut.clk, 20)
    assert int(dut.timer_irq.value) == 0, "irq asserted before mtime reached mtimecmp"
    await ClockCycles(dut.clk, 30)
    assert int(dut.timer_irq.value) == 1, "irq not asserted after mtime passed mtimecmp"
    await ClockCycles(dut.clk, 10)
    assert int(dut.timer_irq.value) == 1, "irq must stay asserted while mtime >= mtimecmp"
    await write(dut, MTIMECMP_HI, 1)  # mtimecmp now 2^32 above mtime
    await ClockCycles(dut.clk, 2)
    assert int(dut.timer_irq.value) == 0, "irq must clear after mtimecmp is moved past mtime"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtime_low_write_does_not_carry(dut):
    """Writing mtime's low word while it is 0xFFFFFFFF leaves the high word alone.

    The write and that edge's +1 land on the same edge: the written low word wins,
    and the increment's carry must not leak into the high word.
    """
    await setup(dut)
    await write(dut, MTIME_HI, 7)
    await write(dut, MTIME, ALL_ONES - 1)  # the next edge (between writes) makes lo 0xFFFFFFFF
    await write(dut, MTIME, 0x100)  # this edge would carry lo 0xFFFFFFFF -> 0 into hi
    assert await read(dut, MTIME_HI) == 7, "low-word write must not carry into the high word"
    lo = await read(dut, MTIME)
    assert 0x100 <= lo < 0x108, f"low word after write: 0x{lo:08X}"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_clint_offsets(dut):
    """mtimecmp at 0x4000/0x4004, mtime at 0xBFF8/0xBFFC, each also at the byte addresses
    inside its word"""
    await setup(dut)
    await write(dut, MTIMECMP, 0x1122_3344)
    await write(dut, MTIMECMP_HI + 3, 0x5566_7788)  # a byte address in the hi word
    assert await read(dut, MTIMECMP) == 0x1122_3344
    assert await read(dut, MTIMECMP_HI) == 0x5566_7788
    assert await read(dut, MTIMECMP + 1) == 0x1122_3344
    assert await read(dut, MTIMECMP + 6) == 0x5566_7788
    await write(dut, MTIMECMP + 3, 0xA5A5_0000)
    assert await read(dut, MTIMECMP + 2) == 0xA5A5_0000
    await write(dut, MTIME_HI, 9)
    await write(dut, MTIME + 2, 0x100)
    lo = await read(dut, MTIME)
    assert 0x100 <= lo < 0x108, f"mtime lo: 0x{lo:08X}"
    assert await read(dut, MTIME_HI) == 9
    assert await read(dut, MTIME_HI + 1) == 9
    lo_byte = await read(dut, MTIME + 3)
    assert lo_byte == lo + 6, "a byte address reads the same mtime (three reads, two edges each, later)"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_clint_undefined_offsets(dut):
    """Every CLINT offset that is no register reads 0 and ignores writes (0x0004-0x000C were
    the TIMER page's mtime hi and mtimecmp, which the CLINT doesn't have there)"""
    await setup(dut)
    await write(dut, MTIMECMP, 0x1234_5678)
    for off in (0x0004, 0x0008, 0x000C, 0x0010, 0x1000, 0x3FFC, 0x4008, 0x8000, 0xBFF0, 0xBFF4, 0xC000, 0xFFFC):
        await write(dut, off, ALL_ONES)
        assert await read(dut, off) == 0, f"CLINT offset 0x{off:04X} must read 0"
    assert await read(dut, MTIMECMP) == 0x1234_5678
    assert await read(dut, MTIMECMP_HI) == ALL_ONES
    assert await read(dut, MSIP) == 0
    assert int(dut.msip.value) == 0
    assert await read(dut, MTIME_HI) == 0, "mtime hi untouched"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_msip(dut):
    """msip: bit 0 of the written word (any byte address of word 0), read back alone, on the
    output from the edge after the write"""
    await setup(dut)
    await FallingEdge(dut.clk)
    dut.addr.value, dut.data_in.value, dut.we.value = MSIP, ALL_ONES, 1
    await FallingEdge(dut.clk)  # the write's edge has passed
    dut.we.value = 0
    assert int(dut.msip.value) == 1, "msip shows the write after its edge"
    assert await read(dut, MSIP) == 1, "only bit 0 reads back"
    await write(dut, MSIP + 2, 0xFFFF_FFFE)  # bit 0 clear, at a byte address
    assert int(dut.msip.value) == 0
    await write(dut, MSIP + 3, 1)
    assert int(dut.msip.value) == 1
    await write(dut, 0x0004, 0)  # the next word is no register
    assert int(dut.msip.value) == 1
    assert await read(dut, MSIP) == 1
    await write(dut, MSIP, 0)
    assert int(dut.msip.value) == 0
    assert await read(dut, MSIP) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_mtip_through_the_clint(dut):
    """timer_irq from mtimecmp written low half first, cleared by its high half"""
    await setup(dut)
    now = await read(dut, MTIME)
    await write(dut, MTIMECMP, now + 30)
    await write(dut, MTIMECMP_HI, 0)
    assert int(dut.timer_irq.value) == 0
    await ClockCycles(dut.clk, 40)
    assert int(dut.timer_irq.value) == 1
    await write(dut, MTIMECMP_HI, ALL_ONES)
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


# The CLINT's cases: each register (or none) read and written; msip set and cleared;
# timer_irq rising
@fcov.point(
    "clint.access",
    [(r, w) for r in REGS for w in (0, 1)],
    xf=lambda reg, we, msip_change, mtip_rise: (reg or "none", we),
)
@fcov.point("clint.msip", ("set", "clear"), xf=lambda reg, we, msip_change, mtip_rise: msip_change)
@fcov.point("clint.mtip_rise", ("clint",), xf=lambda reg, we, msip_change, mtip_rise: mtip_rise)
def sample(reg, we, msip_change, mtip_rise):
    pass


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_random_against_model(dut):
    """Random reads and writes (every register, byte addresses inside them, unmapped
    offsets), with values aimed at the low word's carry and at mtimecmp just around mtime,
    checked every cycle. Starts with a directed MTIP rise, so the fcov bin doesn't depend
    on the seed."""
    await setup(dut)
    # Known state: write mtime hi, then lo (the lo write drops that edge's increment)
    await write(dut, MTIME_HI, 0)
    await write(dut, MTIME, 0)
    await write(dut, MTIMECMP, ALL_ONES)
    await write(dut, MTIMECMP_HI, ALL_ONES)
    # write() waits for a falling edge, writes on the next rising one and returns at the
    # falling edge after it: each mtimecmp write takes two edges, so mtime is 4 by now
    model = TimerModel(4, (1 << 64) - 1)

    directed = [(1, MTIMECMP, 40), (1, MTIMECMP_HI, 0)] + [(0, MTIME, 0)] * 40
    directed += [(1, MTIMECMP_HI, ALL_ONES), (1, MSIP, 1), (1, MSIP, 0)]
    # Every CLINT register (and an unmapped offset) read and written at least once
    directed += [(0, a, 0) for a in (*CLINT_REGS, 0x8000)]
    directed += [(1, MTIME_HI, 0), (1, MTIME, 100), (1, 0x8000, ALL_ONES)]
    for k in range(len(directed) + 4000):
        if k < len(directed):
            we, addr, data = directed[k]
        else:
            if random.random() < 0.85:
                addr = random.choice(list(CLINT_REGS)) | random.randrange(4)
            else:
                addr = random.getrandbits(16)
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
        reg = decode(addr)
        dut.we.value, dut.addr.value, dut.data_in.value = int(we), addr, data
        old_msip, old_irq = model.msip, int(model.mtime >= model.mtimecmp)
        out, irq, msip = model.edge(we, reg, data)
        await FallingEdge(dut.clk)
        ctx = f"we={int(we)} addr=0x{addr:04x} data=0x{data:08x}"
        assert int(dut.data_out.value) == out, f"data_out 0x{int(dut.data_out.value):08x} != 0x{out:08x}, {ctx}"
        assert int(dut.timer_irq.value) == irq, f"timer_irq, {ctx}"
        assert int(dut.msip.value) == msip, f"msip, {ctx}"
        new_irq = int(model.mtime >= model.mtimecmp)  # timer_irq after the next edge
        msip_change = {(0, 1): "set", (1, 0): "clear"}.get((old_msip, msip))
        sample(reg, int(we), msip_change, "clint" if new_irq > old_irq else None)
    fcov.export()
