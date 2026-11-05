# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
uart_regs (the UART's registers in LiteX liteuart's layout, soc/uart/uart_regs.veryl) unit
tests, against a model of README's table.

Register index addr[7:2] in RV32, Hack's word k (addr[1:0]) as index k; byte offsets
(addr[1:0] in RV32) are not looked at. 0 RXTX (a read pops: rx_pop, and reads 0 when empty; a
write pushes: tx_push), 1 TXFULL, 2 RXEMPTY, 3 EV_STATUS (the events {RX = !rx_empty, TX =
!tx_full}), 4 EV_PENDING (EV_STATUS while EV_ENABLE is 0, EV_STATUS & EV_ENABLE otherwise),
5 EV_ENABLE (2 bits, read/write), 6 TXEMPTY, 7 RXFULL, 8-63 read 0; writes to anything but
RXTX and EV_ENABLE are ignored. irq = |(EV_STATUS & EV_ENABLE), combinational (0 with
EV_ENABLE 0, whatever EV_PENDING shows). EV_ENABLE resets with rst and with soft_rst. A read
shows the register chosen by the address and select of the cycle before (registered here),
combinationally from the FIFO flags of its own cycle. The 256-byte repeat is soc_top's (it
passes addr[7:0] only): the SoC tests check it.
"""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer

REGS = ("RXTX", "TXFULL", "RXEMPTY", "EV_STATUS", "EV_PENDING", "EV_ENABLE", "TXEMPTY", "RXFULL")
RXTX, TXFULL, RXEMPTY, EV_STATUS, EV_PENDING, EV_ENABLE, TXEMPTY, RXFULL = range(8)
# The FIFO flags a FIFO can show: (full, empty) never both
FIFO_STATES = ((0, 1), (0, 0), (1, 0))


def pending(en, events):
    """EV_PENDING: the levels while nothing is enabled (Linux's polled driver), else the
    enabled ones (Zephyr's uart_console ISR)."""
    return events if en == 0 else en & events


def reg_name(idx):
    return REGS[idx] if idx < len(REGS) else "none"


def index(addr, hack):
    """The register index of an address's low byte."""
    return addr & 3 if hack else addr >> 2 & 0x3F


class Flags:
    """The FIFO flags uart_controller drives, and the RX FIFO's head."""

    def __init__(self, tx=(0, 1), rx=(0, 1), data=0):
        (self.tx_full, self.tx_empty), (self.rx_full, self.rx_empty) = tx, rx
        self.data = data

    def events(self):
        return (1 - self.rx_empty) << 1 | (1 - self.tx_full)

    def drive(self, dut):
        dut.tx_full.value, dut.tx_empty.value = self.tx_full, self.tx_empty
        dut.rx_full.value, dut.rx_empty.value = self.rx_full, self.rx_empty
        dut.rx_data.value = self.data

    @staticmethod
    def random():
        return Flags(random.choice(FIFO_STATES), random.choice(FIFO_STATES), random.getrandbits(8))


class Model:
    """uart_regs per cycle: outputs from the state before the rising edge, then the edge."""

    def __init__(self):
        self.last_sel, self.last_idx, self.ev_en = 0, 0, 0

    def outputs(self, f, read, we, wr_idx):
        words = {
            RXTX: 0 if f.rx_empty else f.data,  # 0 on an empty FIFO (it shows a stale slot)
            TXFULL: f.tx_full,
            RXEMPTY: f.rx_empty,
            EV_STATUS: f.events(),
            EV_PENDING: pending(self.ev_en, f.events()),
            EV_ENABLE: self.ev_en,
            TXEMPTY: f.tx_empty,
            RXFULL: f.rx_full,
        }
        rdata = words.get(self.last_idx, 0) if self.last_sel else 0
        tx_push = int(we and wr_idx == RXTX)
        rx_pop = int(read and self.last_sel and self.last_idx == RXTX)
        irq = int(self.ev_en & f.events() != 0)
        return rdata, tx_push, rx_pop, irq

    def edge(self, soft_rst, rd_sel, rd_idx, we, wr_idx, wdata):
        self.last_sel, self.last_idx = rd_sel, rd_idx
        if soft_rst:
            self.ev_en = 0
        elif we and wr_idx == EV_ENABLE:
            self.ev_en = wdata & 3


IDLE = dict(soft_rst=0, hack_mode=0, rd_sel=0, rd_addr=0, read=0, we=0, wr_addr=0, wdata=0)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    for name, v in IDLE.items():
        getattr(dut, name).value = v
    Flags().drive(dut)
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    return Model()


async def cycle(dut, model, flags, **kw):
    """Apply one cycle's inputs (IDLE's unless given) after a falling edge, check the
    combinational outputs against the model, and let the rising edge pass; returns the
    outputs checked."""
    s = {**IDLE, **kw}
    for name, v in s.items():
        getattr(dut, name).value = v
    flags.drive(dut)
    await Timer(1, unit="ns")
    hack = s["hack_mode"]
    rd_idx, wr_idx = index(s["rd_addr"], hack), index(s["wr_addr"], hack)
    want = model.outputs(flags, s["read"], s["we"], wr_idx)
    got = tuple(int(getattr(dut, n).value) for n in ("rdata", "tx_push", "rx_pop", "irq"))
    ctx = f"{s} last=({model.last_sel},{model.last_idx}) ev_en={model.ev_en}"
    assert got == want, f"(rdata, tx_push, rx_pop, irq) {got} != {want}, {ctx}"
    model.edge(s["soft_rst"], s["rd_sel"], rd_idx, s["we"], wr_idx, s["wdata"])
    await FallingEdge(dut.clk)
    return got


async def read_reg(dut, model, flags, addr, hack=0):
    """Present a read of addr, then return the word shown in the next cycle."""
    await cycle(dut, model, flags, rd_sel=1, rd_addr=addr, hack_mode=hack)
    return (await cycle(dut, model, flags, read=1))[0]


async def write_reg(dut, model, flags, addr, value, hack=0):
    return await cycle(dut, model, flags, we=1, wr_addr=addr, wdata=value, hack_mode=hack)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_reset_values(dut):
    """After reset nothing is selected (rdata 0), EV_ENABLE is 0 and no event raises irq"""
    model = await setup(dut)
    busy = Flags(tx=(0, 0), rx=(1, 0), data=0x5A)
    rdata, _, rx_pop, irq = await cycle(dut, model, busy, read=1)
    assert (rdata, rx_pop, irq) == (0, 0, 0)
    assert await read_reg(dut, model, busy, 4 * EV_ENABLE) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_every_register(dut):
    """Each of the 64 word offsets at each byte offset, with each FIFO state: the eight
    registers, then zeros"""
    model = await setup(dut)
    for tx in FIFO_STATES:
        for rx in FIFO_STATES:
            flags = Flags(tx, rx, data=random.getrandbits(8))
            for idx in range(64):
                for byte in (0, 3) if idx < 9 else (random.randrange(4),):
                    await read_reg(dut, model, flags, 4 * idx + byte)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_only_rxtx_transmits_and_pops(dut):
    """tx_push for a store to RXTX (any byte offset) only; rx_pop for a taken read of RXTX
    only: not for the other registers, not without the select, not without the CPU's read"""
    model = await setup(dut)
    flags = Flags(rx=(0, 0), data=0x41)
    for idx in range(64):
        for byte in (0, 1, 2, 3):
            assert (await write_reg(dut, model, flags, 4 * idx + byte, 0))[1] == int(idx == RXTX)
    for idx in range(64):
        await cycle(dut, model, flags, rd_sel=1, rd_addr=4 * idx)
        assert (await cycle(dut, model, flags, read=1))[2] == int(idx == RXTX)
    await cycle(dut, model, flags, rd_sel=1, rd_addr=0)
    assert (await cycle(dut, model, flags, read=0))[2] == 0, "no read, no pop"
    await cycle(dut, model, flags, rd_sel=0, rd_addr=0)
    assert (await cycle(dut, model, flags, read=1))[2] == 0, "not selected, no pop"


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_ev_enable_and_irq(dut):
    """EV_ENABLE takes wdata[1:0] (byte offsets too); EV_PENDING reads the events with
    EV_ENABLE 0 and EV_ENABLE & events otherwise, irq is the OR of EV_ENABLE & events, for
    every pair of them, at once (no latching: the events are levels)"""
    model = await setup(dut)
    for en in range(4):
        await write_reg(dut, model, Flags(), 4 * EV_ENABLE + random.randrange(4), en)
        for tx in FIFO_STATES:
            for rx in FIFO_STATES:
                flags = Flags(tx, rx)
                assert (await cycle(dut, model, flags))[3] == int(en & flags.events() != 0)
                assert await read_reg(dut, model, flags, 4 * EV_PENDING) == pending(en, flags.events())
                assert await read_reg(dut, model, flags, 4 * EV_STATUS) == flags.events()
                sample_pending((en, flags.events()))
        assert await read_reg(dut, model, Flags(), 4 * EV_ENABLE) == en
    # Writes elsewhere leave it; EV_PENDING ignores writes (nothing to clear)
    await write_reg(dut, model, Flags(), 4 * EV_ENABLE, 2)
    for idx in (EV_STATUS, EV_PENDING, TXEMPTY, 0x15, 0x3D):
        await write_reg(dut, model, Flags(), 4 * idx, 0)
    assert await read_reg(dut, model, Flags(), 4 * EV_ENABLE) == 2
    flags = Flags(rx=(0, 0))
    await write_reg(dut, model, flags, 4 * EV_PENDING, 3)
    assert (await cycle(dut, model, flags))[3] == 1, "EV_PENDING ignores writes"
    assert await read_reg(dut, model, flags, 4 * EV_PENDING) == 2, "RX only: TX not enabled"
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_soft_reset_clears_ev_enable(dut):
    """The soft reset clears EV_ENABLE (also over a store to it in the same cycle); a reset
    mid-run clears it and the read select at once"""
    model = await setup(dut)
    flags = Flags()
    await write_reg(dut, model, flags, 4 * EV_ENABLE, 3)
    assert (await cycle(dut, model, flags))[3] == 1
    await cycle(dut, model, flags, soft_rst=1)
    assert await read_reg(dut, model, flags, 4 * EV_ENABLE) == 0
    await write_reg(dut, model, flags, 4 * EV_ENABLE, 1)
    await cycle(dut, model, flags, soft_rst=1, we=1, wr_addr=4 * EV_ENABLE, wdata=3)
    assert (await cycle(dut, model, flags))[3] == 0
    # The power-on reset (asynchronous)
    await write_reg(dut, model, flags, 4 * EV_ENABLE, 3)
    await cycle(dut, model, flags, rd_sel=1, rd_addr=4 * EV_ENABLE)
    dut.rst.value = 0
    await Timer(1, unit="ns")
    assert (int(dut.rdata.value), int(dut.irq.value)) == (0, 0)
    await ClockCycles(dut.clk, 2, rising=False)
    dut.rst.value = 1
    model.__init__()
    assert await read_reg(dut, model, flags, 4 * EV_ENABLE) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_hack_words(dut):
    """Hack's words 0x6000-0x6003 (addr[1:0]) are RXTX, TXFULL, RXEMPTY, EV_STATUS; bits above
    them don't matter (soc_addr_decoder has already selected the UART), so EV_ENABLE can't be
    reached and only word 0 transmits"""
    model = await setup(dut)
    flags = Flags(tx=(1, 0), rx=(0, 0), data=0x68)
    for k in range(4):
        for high in (0x00, 0x14, 0xFC):
            await read_reg(dut, model, flags, high | k, hack=1)
            assert (await write_reg(dut, model, flags, high | k, 3, hack=1))[1] == int(k == 0)
    assert await read_reg(dut, model, flags, 4 * EV_ENABLE) == 0


# The cases: each register (or none) read and written; EV_ENABLE x events (irq's inputs);
# EV_PENDING read with each EV_ENABLE x events (the levels with EV_ENABLE 0, masked
# otherwise); irq rising and falling; the soft reset clearing an enabled EV_ENABLE
@fcov.point("liteuart.access", [(r, op) for r in (*REGS, "none") for op in ("read", "write")])
def sample_access(access):
    pass


@fcov.point("liteuart.pending_read", [(en, ev) for en in range(4) for ev in range(4)])
def sample_pending(en_ev):
    pass


@fcov.point(
    "liteuart.enable_event",
    [(en, ev) for en in range(4) for ev in range(4)],
    xf=lambda en_ev, irq_change, soft: en_ev,
)
@fcov.point("liteuart.irq", ("rise", "fall"), xf=lambda en_ev, irq_change, soft: irq_change)
@fcov.point("liteuart.soft_reset", ("clears",), xf=lambda en_ev, irq_change, soft: soft)
def sample(en_ev, irq_change, soft):
    pass


STEPS = 6000  # random cycles (with the directed ones before them)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_random_against_model(dut):
    """Random reads, stores, FIFO flags, Hack mode and soft resets, checked every cycle.
    Starts with every register read and written in RV32, so the access bins don't depend on
    the seed."""
    model = await setup(dut)
    directed = [dict(rd_sel=1, rd_addr=4 * i, we=1, wr_addr=4 * i, wdata=i & 3) for i in (*range(8), 9)]
    directed += [dict(read=1), dict(we=1, wr_addr=4 * EV_ENABLE, wdata=3), dict(soft_rst=1)]
    prev_irq = 0
    for k in range(len(directed) + STEPS):
        if k < len(directed):
            kw = directed[k]
        else:
            hack = int(random.random() < 0.2)
            kw = dict(
                hack_mode=hack,
                rd_sel=int(random.random() < 0.6),
                rd_addr=random.choice((random.randrange(0x20), random.getrandbits(8))),
                read=int(random.random() < 0.5),
                we=int(random.random() < 0.3),
                wr_addr=random.choice((4 * EV_ENABLE, random.randrange(0x20), random.getrandbits(8))),
                wdata=random.getrandbits(2),
                soft_rst=int(random.random() < 0.02),
            )
        flags = Flags.random()
        s = {**IDLE, **kw}
        rd = reg_name(model.last_idx) if model.last_sel else None
        hack = s["hack_mode"]
        wr = reg_name(index(s["wr_addr"], hack)) if s["we"] else None
        en_ev = (model.ev_en, flags.events())
        soft = "clears" if s["soft_rst"] and model.ev_en else None
        irq = (await cycle(dut, model, flags, **kw))[3]
        if rd:
            sample_access((rd, "read"))
        if rd == "EV_PENDING":
            sample_pending(en_ev)
        if wr:
            sample_access((wr, "write"))
        sample(en_ev, {(0, 1): "rise", (1, 0): "fall"}.get((prev_irq, irq)), soft)
        prev_irq = irq
    fcov.export()
