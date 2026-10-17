# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_icache: the instruction cache in front of the wait-state window. A CPU model fetches
as unified_cpu does (the line is read a cycle ahead at rd_idx, then FETCH is held until
instr_ready), sometimes completing a store to the window in the cycle before (inv); a
memory model stands in for soc_xmem_bridge and the PSRAM (a miss completes after a random
delay with the word, outside a miss mem_ready is 1, as the bridge's instr_ready). A Python
model of the cache predicts hit, mem_sel and instr_ready in every cycle, and every word a
fetch returns is checked against the memory, so a stale line fails the test."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

LINES = 4096
TAG_SHIFT = 12  # index fetch_addr[11:0] (addr[13:2]), tag fetch_addr[20:12] (addr[22:14])
WORD_MASK = (1 << 21) - 1  # fetch_addr: the PSRAM word, addr[22:2]

OUTCOMES = ("hit", "miss", "outside", "sweep_wait")
INVS = ("none", "other_line", "same_line")


@fcov.point("icache.outcome", OUTCOMES, xf=lambda outcome, inv, ahead, evict: outcome)
@fcov.point("icache.inv", INVS, xf=lambda outcome, inv, ahead, evict: inv)
@fcov.point("icache.read_ahead", ("match", "mismatch"), xf=lambda outcome, inv, ahead, evict: ahead)
@fcov.point("icache.fill", ("empty", "replace", "none"), xf=lambda outcome, inv, ahead, evict: evict)
def sample(outcome, inv, ahead, evict):
    pass


def idx_of(word):
    return word & (LINES - 1)


def tag_of(word):
    return word >> TAG_SHIFT


class Bench:
    def __init__(self, dut, max_delay=6):
        self.dut = dut
        self.max_delay = max_delay
        self.mem = {}  # PSRAM word -> value
        # The cache model: line -> tag of a valid line
        self.tags = {}
        self.swept = False
        self.sweep_idx = 0
        self.rd_idx_q = 0
        self.conflict_q = False
        self.missing = False  # a miss at the bridge stays one until it completes
        self.due = None  # cycle the pending miss completes
        self.cycle = 0
        self.clocked = False

    def word(self, w):
        if w not in self.mem:
            self.mem[w] = random.getrandbits(32)
        return self.mem[w]

    async def reset(self):
        """rst for a cycle (soc_top's rst_n): the registers reset, the RAMs keep their lines
        until the next sweep."""
        d = self.dut
        if not self.clocked:
            cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
            self.clocked = True
        await FallingEdge(d.clk)
        self.swept, self.sweep_idx, self.rd_idx_q, self.conflict_q, self.missing, self.due = (
            False, 0, 0, False, False, None)  # fmt: skip
        d.rst.value = 0
        d.clear.value = 0
        self.drive()
        d.mem_ready.value = 1
        d.mem_rdata.value = 0
        await FallingEdge(d.clk)
        d.rst.value = 1

    def drive(self, rd_idx=None, fetch=False, sel=False, addr=0, inv=False, inv_idx=0):
        d = self.dut
        d.rd_idx.value = random.randrange(LINES) if rd_idx is None else rd_idx
        d.fetch_req.value = int(fetch)
        # Outside FETCH fetch_sel still follows pc_reg (in the window or not), as in soc_top
        d.fetch_sel.value = int(sel) if fetch else random.getrandbits(1)
        d.fetch_addr.value = addr if fetch else random.getrandbits(21)
        d.inv.value = int(inv)
        d.inv_idx.value = inv_idx if inv else random.randrange(LINES)

    async def tick(self, clear=0, rd_idx=None, fetch=False, sel=False, addr=0, inv=False, inv_idx=0):
        """One cycle against the model; returns (instr_ready, hit)."""
        d, c = self.dut, self.cycle
        d.clear.value = clear
        self.drive(rd_idx, fetch, sel, addr, inv, inv_idx)
        await Timer(1, unit="ns")
        rd = int(d.rd_idx.value)
        fa = int(d.fetch_addr.value)
        idx = idx_of(fa)

        line_ok = self.rd_idx_q == idx and self.tags.get(idx) == tag_of(fa)
        hit = self.swept and not self.missing and not self.conflict_q and line_ok
        win = fetch and sel
        want_sel = win and self.swept and not hit
        ctx = f"cycle {c}: fetch={fetch} sel={sel} addr={fa:06x} rd_idx={rd} inv={inv}/{inv_idx}"
        assert int(d.hit.value) == hit, f"{ctx}: hit={int(d.hit.value)}, model {hit}"
        assert int(d.mem_sel.value) == want_sel, f"{ctx}: mem_sel={int(d.mem_sel.value)}"

        # The bridge: ready outside a miss, else when the miss's response comes
        done = False
        if want_sel:
            if self.due is None:
                self.due = c + random.randint(1, self.max_delay)
            done = self.due == c
        mem_ready = done or not want_sel
        d.mem_ready.value = int(mem_ready)
        d.mem_rdata.value = self.word(fa) if done else random.getrandbits(32)
        await Timer(1, unit="ns")

        ready = not win or (self.swept and (hit or mem_ready))
        assert int(d.instr_ready.value) == ready, f"{ctx}: instr_ready={int(d.instr_ready.value)}"
        if win and ready:
            got = int(d.rdata.value)
            assert got == self.word(fa), f"{ctx}: rdata {got:08x}, memory {self.word(fa):08x}"

        # The model's writes in this cycle (one tag write port)
        fill = win and self.swept and not hit and mem_ready
        tag_we, waddr = False, 0
        if not self.swept:
            if clear:
                tag_we, waddr = True, self.sweep_idx
                self.tags.pop(self.sweep_idx, None)
                self.sweep_idx += 1
                self.swept = self.sweep_idx == LINES
        elif fill:
            tag_we, waddr = True, idx
            self.tags[idx] = tag_of(fa)
            self.due = None
        elif inv:
            tag_we, waddr = True, inv_idx
            self.tags.pop(inv_idx, None)
        self.missing = want_sel and not mem_ready
        self.rd_idx_q = rd
        self.conflict_q = tag_we and waddr == rd

        await FallingEdge(d.clk)
        self.cycle += 1
        return ready, hit

    async def sweep(self):
        for _ in range(LINES):
            await self.tick(clear=1)
        assert self.swept

    async def fetch(self, word, sel=True, inv=None, ahead=True, clear=1):
        """The cycle before FETCH (EXECUTE, or a store's last MEM_WAIT cycle when inv is a
        word being stored), then FETCH until ready. ahead=False reads another line ahead (a
        path where pc_out isn't the next pc_reg), which must not hit."""
        idx = idx_of(word)
        rd = idx if ahead else (idx + random.randrange(1, LINES)) % LINES
        inv_kind = "none"
        if inv is not None:
            self.mem[inv] = random.getrandbits(32)  # the store's new word
            inv_kind = "same_line" if idx_of(inv) == idx else "other_line"
        filled = self.tags.get(idx)
        await self.tick(clear, rd, inv=inv is not None, inv_idx=idx_of(inv or 0))
        swept = self.swept
        hit = None
        for _ in range(LINES + 100):  # the sweep, or a miss
            ready, h = await self.tick(clear, idx, True, sel, word)
            if hit is None:
                hit = h
            if ready:
                if not sel:
                    outcome = "outside"
                elif not swept:
                    outcome = "sweep_wait"
                else:
                    outcome = "hit" if hit else "miss"
                fill = "none" if outcome != "miss" else ("empty" if filled is None else "replace")
                sample(outcome, inv_kind, "match" if ahead else "mismatch", fill)
                return outcome
        raise AssertionError(f"fetch of {word:06x} never completed")


def pool(n_lines=6, n_tags=3):
    """Words on a few lines with a few tags each: hits, conflicts and evictions."""
    lines = random.sample(range(LINES), n_lines)
    tags = random.sample(range(1 << (21 - TAG_SHIFT)), n_tags)
    return [t << TAG_SHIFT | ln for ln in lines for t in tags]


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_directed(dut):
    """A fetch while the lines are swept waits; then miss, hit, eviction by another tag, a
    store to the line (and to another), a store to the next fetch's line in the cycle it is
    read (the board leaves that read undefined), a stale read-ahead, and a fetch outside the
    window"""
    b = Bench(dut, max_delay=3)
    await b.reset()
    # Before clear: no sweep yet, a window fetch waits without asking the memory
    for _ in range(3):
        ready, _ = await b.tick(0, 5, True, True, 5)
        assert not ready
    # During the sweep (clear high) it still waits, and completes once the sweep ends
    await b.fetch(0x1_2005)
    assert b.swept
    w = 0x0_4007
    await b.fetch(w)  # miss
    await b.fetch(w)  # hit
    await b.fetch(w, inv=w + (1 << TAG_SHIFT))  # a store to the same line (another tag): invalidated
    await b.fetch(w)  # miss again
    await b.fetch(w, inv=0x0_0123)  # a store to another line leaves it
    await b.fetch(w + (2 << TAG_SHIFT))  # another tag on the line: replaces it
    await b.fetch(w)
    await b.fetch(w, inv=w)  # the store rewrites the very word: the fetch gets the new one
    await b.fetch(w)
    await b.fetch(w, ahead=False)  # a stale read-ahead misses
    await b.fetch(w, sel=False)  # outside the window
    await b.fetch(w)
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_random(dut):
    """Random fetches over a few lines and tags, stores in between, stale read-aheads and
    fetches outside the window, against the model"""
    b = Bench(dut)
    await b.reset()
    await b.sweep()
    words = pool()
    for _ in range(1500):
        word = random.choice(words)
        inv = random.choice(words) if random.random() < 0.25 else None
        await b.fetch(word, sel=random.random() < 0.9, inv=inv, ahead=random.random() < 0.9,
                      clear=random.randint(0, 1))  # fmt: skip
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_sweeps_valid_lines(dut):
    """rst over a cache whose every line is valid: the RAMs keep their contents, and the
    next sweep must clear every one of them, whatever order it takes, so each fetch misses"""
    b = Bench(dut, max_delay=2)
    await b.reset()
    await b.sweep()
    words = [random.getrandbits(21 - TAG_SHIFT) << TAG_SHIFT | ln for ln in range(LINES)]
    for w in words:
        assert await b.fetch(w) == "miss"
    assert len(b.tags) == LINES
    await b.reset()
    await b.sweep()
    for w in random.sample(words, LINES):
        assert await b.fetch(w) == "miss", f"{w:06x} hit after the sweep"
    fcov.export()
