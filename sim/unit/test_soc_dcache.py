# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_dcache: the write-through data cache in front of the wait-state window. A CPU model
makes loads and stores as unified_cpu does (the line is read a cycle ahead at rd_idx, in
EXECUTE, then MEM_WAIT is held until data_ready); a memory model stands in for
soc_xmem_bridge and the PSRAM (a load miss or a store completes after a random delay, a
store's bytes reach the memory then; outside those mem_ready is 1, as the bridge's
data_ready). A Python model of the cache predicts hit, mem_sel and data_ready in every
cycle, and every word a load returns is checked against the memory, so a stale line or a
wrong byte merge fails the test."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

LINES = 512

KINDS = ("load", "store")
OUTCOMES = ("hit", "miss", "outside", "sweep_wait")


# Per access: its kind and outcome; for stores, what happened to the line (a hit merges,
# a full-word miss allocates, a partial miss invalidates it); for load misses, whether the
# line held another tag
@fcov.point("dcache.kind", KINDS, xf=lambda kind, outcome, line, ahead: kind)
@fcov.point("dcache.outcome", OUTCOMES, xf=lambda kind, outcome, line, ahead: outcome)
@fcov.cross("dcache.kind_x_outcome", ("dcache.kind", "dcache.outcome"))
@fcov.point(
    "dcache.line",
    ("merge_full", "merge_partial", "allocate", "invalidate", "fill_empty", "fill_replace", "none"),
    xf=lambda kind, outcome, line, ahead: line,
)
@fcov.point("dcache.read_ahead", ("match", "mismatch"), xf=lambda kind, outcome, line, ahead: ahead)
def sample(kind, outcome, line, ahead):
    pass


def idx_of(word):
    return word & (LINES - 1)


def tag_of(word):
    return word >> 9


def merge(old, new, strobe):
    out = old
    for i in range(4):
        if strobe >> i & 1:
            mask = 0xFF << (8 * i)
            out = out & ~mask | new & mask
    return out


class Bench:
    def __init__(self, dut, max_delay=6):
        self.dut = dut
        self.max_delay = max_delay
        self.mem = {}  # PSRAM word -> value
        self.tags = {}  # the cache model: line -> tag of a valid line
        self.swept = False
        self.sweep_idx = 0
        self.rd_idx_q = 0
        self.conflict_q = False
        self.missing = False  # an access at the bridge stays a miss until it completes
        self.due = None  # cycle the pending bridge access completes
        self.cycle = 0

    def word(self, w):
        if w not in self.mem:
            self.mem[w] = random.getrandbits(32)
        return self.mem[w]

    async def reset(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
        await FallingEdge(d.clk)
        d.rst.value = 0
        d.clear.value = 0
        self.drive()
        d.mem_ready.value = 1
        d.mem_rdata.value = 0
        await FallingEdge(d.clk)
        d.rst.value = 1

    def drive(self, rd_idx=None, kind=None, sel=False, addr=0, wdata=0, wstrb=0):
        d = self.dut
        d.rd_idx.value = random.randrange(LINES) if rd_idx is None else rd_idx
        d.data_req.value = int(kind is not None)
        d.data_sel.value = int(kind is not None and sel)
        d.data_addr.value = addr if kind is not None else random.getrandbits(21)
        d.data_we.value = int(kind == "store")
        d.data_wdata.value = wdata if kind == "store" else random.getrandbits(32)
        d.data_wstrb.value = wstrb if kind == "store" else random.randrange(16)

    async def tick(self, clear=0, rd_idx=None, kind=None, sel=False, addr=0, wdata=0, wstrb=0):
        """One cycle against the model; returns (data_ready, hit)."""
        d, c = self.dut, self.cycle
        d.clear.value = clear
        self.drive(rd_idx, kind, sel, addr, wdata, wstrb)
        await Timer(1, unit="ns")
        rd = int(d.rd_idx.value)
        wa = int(d.data_addr.value)
        idx = idx_of(wa)
        store = kind == "store"

        line_ok = self.rd_idx_q == idx and self.tags.get(idx) == tag_of(wa)
        hit = self.swept and not self.missing and not self.conflict_q and line_ok
        win = kind is not None and sel
        want_sel = win and self.swept and (store or not hit)
        ctx = f"cycle {c}: {kind} sel={sel} addr={wa:06x} rd_idx={rd} wstrb={wstrb:04b}"
        assert int(d.hit.value) == hit, f"{ctx}: hit={int(d.hit.value)}, model {hit}"
        assert int(d.mem_sel.value) == want_sel, f"{ctx}: mem_sel={int(d.mem_sel.value)}"

        done = False
        if want_sel:
            if self.due is None:
                self.due = c + random.randint(1, self.max_delay)
            done = self.due == c
        mem_ready = done or not want_sel
        d.mem_ready.value = int(mem_ready)
        d.mem_rdata.value = self.word(wa) if done and not store else random.getrandbits(32)
        await Timer(1, unit="ns")

        ready = not win or (self.swept and (hit and not store or mem_ready))
        assert int(d.data_ready.value) == ready, f"{ctx}: data_ready={int(d.data_ready.value)}"
        if win and ready and not store:
            got = int(d.rdata.value)
            assert got == self.word(wa), f"{ctx}: rdata {got:08x}, memory {self.word(wa):08x}"

        # The model's writes in this cycle
        we, waddr = False, idx
        if not self.swept:
            if clear:
                we, waddr = True, self.sweep_idx
                self.tags.pop(self.sweep_idx, None)
                self.sweep_idx += 1
                self.swept = self.sweep_idx == LINES
        elif win and done:
            self.due = None
            if not store:
                we = True
                self.tags[idx] = tag_of(wa)
            else:
                self.mem[wa] = merge(self.word(wa), wdata, wstrb)  # write-through
                if hit:
                    we = True
                else:
                    we = True  # a full word allocates, a partial one invalidates
                    if wstrb == 0xF:
                        self.tags[idx] = tag_of(wa)
                    else:
                        self.tags.pop(idx, None)
        self.missing = want_sel and not mem_ready
        self.rd_idx_q = rd
        self.conflict_q = we and waddr == rd

        await FallingEdge(d.clk)
        self.cycle += 1
        return ready, hit

    async def access(self, kind, word, sel=True, wstrb=0xF, ahead=True, clear=1):
        """EXECUTE (the line read ahead), then MEM_WAIT until ready. ahead=False reads
        another line ahead, which must not hit."""
        idx = idx_of(word)
        rd = idx if ahead else (idx + random.randrange(1, LINES)) % LINES
        wdata = random.getrandbits(32)
        held = self.tags.get(idx)
        await self.tick(clear, rd)
        swept = self.swept
        hit = None
        for _ in range(LINES + 100):  # the sweep, or a bridge access
            ready, h = await self.tick(clear, idx, kind, sel, word, wdata, wstrb)
            if hit is None:
                hit = h
            if ready:
                if not sel:
                    outcome = "outside"
                elif not swept:
                    outcome = "sweep_wait"
                else:
                    outcome = "hit" if hit else "miss"
                line = "none"
                if outcome == "hit" and kind == "store":
                    line = "merge_full" if wstrb == 0xF else "merge_partial"
                elif outcome == "miss":
                    if kind == "store":
                        line = "allocate" if wstrb == 0xF else "invalidate"
                    else:
                        line = "fill_empty" if held is None else "fill_replace"
                sample(kind, outcome, line, "match" if ahead else "mismatch")
                return
        raise AssertionError(f"{kind} of {word:06x} never completed")


def pool(n_lines=6, n_tags=3):
    """Words on a few lines with a few tags each: hits, conflicts and evictions."""
    lines = random.sample(range(LINES), n_lines)
    tags = random.sample(range(1 << 12), n_tags)
    return [t << 9 | ln for ln in lines for t in tags]


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_directed(dut):
    """Accesses while the lines are swept wait; then load miss and hit, store hit (full and
    partial: the bytes merge), partial store miss (invalidates the line, even one holding the
    word), full store miss (allocates),
    eviction by another tag, a stale read-ahead, and accesses outside the window"""
    b = Bench(dut, max_delay=3)
    await b.reset()
    for _ in range(3):  # before clear: waits without asking the memory
        ready, _ = await b.tick(0, 5, "load", True, 5)
        assert not ready
    await b.access("load", 0x1_2005)  # waits for the sweep
    assert b.swept
    w = 0x0_4007
    await b.access("load", w)  # miss, fills
    await b.access("load", w)  # hit
    await b.access("store", w, wstrb=0b0010)  # partial store hit: merged
    await b.access("load", w)
    await b.access("store", w, wstrb=0xF)  # full store hit
    await b.access("load", w)
    v = w + (1 << 9)  # same line, another tag
    await b.access("store", v, wstrb=0b1100)  # partial miss: invalidates w's line
    await b.access("load", w)  # miss
    await b.access("load", w, ahead=False)  # a stale read-ahead: miss, refills w
    await b.access("store", w, wstrb=0b0100, ahead=False)  # partial miss on a stale read-ahead: invalidated, not stale
    await b.access("load", w)  # miss
    await b.access("store", v, wstrb=0xF)  # full miss: allocates v over w
    await b.access("load", v)
    await b.access("load", w)  # miss, replaces
    await b.access("load", w, ahead=False)  # a stale read-ahead misses
    await b.access("load", w, sel=False)  # outside the window
    await b.access("store", w, sel=False, wstrb=0b0001)
    await b.access("load", w)
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_random(dut):
    """Random loads and stores (random strobes) over a few lines and tags, stale
    read-aheads and accesses outside the window, against the model"""
    b = Bench(dut)
    await b.reset()
    words = pool()
    await b.access("store", random.choice(words))  # waits for the sweep
    for _ in range(1500):
        kind = random.choice(KINDS)
        wstrb = random.choice((0xF, 0xF, 0x1, 0x2, 0x4, 0x8, 0x3, 0xC, random.randrange(1, 16)))
        await b.access(kind, random.choice(words), sel=random.random() < 0.9, wstrb=wstrb,
                       ahead=random.random() < 0.9, clear=random.randint(0, 1))  # fmt: skip
    fcov.export()
