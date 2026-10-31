# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_icache: the instruction cache in front of the wait-state window, 512 lines of 8 words. A
CPU model fetches as unified_cpu does (the word is read a cycle ahead at rd_idx, then FETCH is
held until instr_ready), sometimes completing a store to the window in the cycle before (inv);
a memory model stands in for soc_xmem_bridge and the PSRAM, on the burst contract as
test_soc_dcache's does (a fill asks for its line's first word, its 8 words come after random
delays, each with mem_ready, which is otherwise low while mem_sel is high and 1 without it, as
the bridge's instr_ready). For the first cycles of some fetches fill_hold is high (soc_dcache's
probe_dirty: the data cache writing back the line): a miss must then wait without asking the
bridge, although its mem_ready is 1. A Python model of the cache predicts hit, miss, mem_sel,
mem_addr and instr_ready in every cycle, and every word a fetch returns is checked against the
memory, so a stale line, a word in the wrong place or a store that misses a word's line
fails the test."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

LINES = 512
LINE_WORDS = 8
WORDS = LINES * LINE_WORDS
TAG_SHIFT = 12  # line fetch_addr[11:3] (addr[13:5]), word [2:0], tag fetch_addr[20:12] (addr[22:14])
WORD_MASK = (1 << 21) - 1  # fetch_addr: the PSRAM word, addr[22:2]
R = 35  # soc_pkg::XMEM_READ_LATENCY
W = 34  # soc_pkg::XMEM_WRITE_LATENCY
FILL = R + 2 * (LINE_WORDS - 1)  # the emulator's XMEM_FETCH_FILL: 49
PROBE_WB = W + 2 * (LINE_WORDS - 1) + 3  # soc_dcache's write-back before the fill

OUTCOMES = ("hit", "miss", "outside", "sweep_wait")
INVS = ("none", "other_line", "same_line", "same_word")


@fcov.point("icache.outcome", OUTCOMES, xf=lambda outcome, inv, ahead, evict: outcome)
@fcov.point("icache.inv", INVS, xf=lambda outcome, inv, ahead, evict: inv)
@fcov.point("icache.read_ahead", ("match", "mismatch"), xf=lambda outcome, inv, ahead, evict: ahead)
@fcov.point("icache.fill", ("empty", "replace", "none"), xf=lambda outcome, inv, ahead, evict: evict)
def sample(outcome, inv, ahead, evict):
    pass


# Per window fetch once the lines are swept: whether fill_hold kept its miss from the bridge
# for at least a cycle
@fcov.point("icache.fill_hold", ("held", "free"), xf=lambda held: held)
def sample_hold(held):
    pass


# Per miss: where in its line the fetch that fills it is; per hit: whether another word of the
# line asked for the fill
@fcov.point("icache.miss_word", tuple(range(LINE_WORDS)), xf=lambda word: word)
def sample_miss_word(word):
    pass


@fcov.point("icache.line_hit", ("same_word", "other_word"), xf=lambda kind: kind)
def sample_line_hit(kind):
    pass


def idx_of(word):
    return word & (WORDS - 1)


def line_of(word):
    return idx_of(word) // LINE_WORDS


def tag_of(word):
    return word >> TAG_SHIFT


class Bench:
    def __init__(self, dut, max_delay=6, controller=False):
        self.dut = dut
        self.max_delay = max_delay
        self.controller = controller  # the words in the controller's burst cycles
        self.mem = {}  # PSRAM word -> value
        # The cache model: line -> tag of a valid line
        self.tags = {}
        self.filled_by = {}  # line -> the word whose miss filled it
        self.swept = False
        self.sweep_idx = 0
        self.rd_idx_q = 0
        self.conflict_q = False
        self.replay_q = False  # the cycle after one in FILL
        self.filling = False
        self.cnt = 0
        self.dues = []  # the cycles the fill's words come in
        self.held = 0  # cycles fill_hold kept a miss from the bridge
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
        self.swept, self.sweep_idx, self.rd_idx_q, self.conflict_q, self.replay_q = False, 0, 0, False, False
        self.filling, self.cnt, self.dues = False, 0, []
        d.rst.value = 0
        d.clear.value = 0
        self.drive()
        d.mem_ready.value = 1
        d.mem_rdata.value = 0
        await FallingEdge(d.clk)
        d.rst.value = 1

    def drive(self, rd_idx=None, fetch=False, sel=False, addr=0, inv=False, inv_idx=0, hold=None):
        d = self.dut
        # Outside a fetch's hold, fill_hold is anything: it only counts for a window miss
        d.fill_hold.value = random.getrandbits(1) if hold is None else int(hold)
        d.rd_idx.value = random.randrange(WORDS) if rd_idx is None else rd_idx
        d.fetch_req.value = int(fetch)
        # Outside FETCH fetch_sel still follows pc_reg (in the window or not), as in soc_top
        d.fetch_sel.value = int(sel) if fetch else random.getrandbits(1)
        d.fetch_addr.value = addr if fetch else random.getrandbits(21)
        d.inv.value = int(inv)
        d.inv_idx.value = inv_idx if inv else random.randrange(LINES)

    def burst(self, start):
        """The cycles of a fill's 8 words: the request is taken in FILL's first cycle, start."""
        if self.controller:
            return [start + R - 3 + 2 * k for k in range(LINE_WORDS)]
        t, dues = start, []
        for _ in range(LINE_WORDS):
            t += random.randint(1, self.max_delay)
            dues.append(t)
        return dues

    async def tick(self, clear=0, rd_idx=None, fetch=False, sel=False, addr=0, inv=False, inv_idx=0, hold=None):
        """One cycle against the model; returns (instr_ready, hit)."""
        d, c = self.dut, self.cycle
        d.clear.value = clear
        self.drive(rd_idx, fetch, sel, addr, inv, inv_idx, hold)
        await Timer(1, unit="ns")
        rd = int(d.rd_idx.value)
        fa = int(d.fetch_addr.value)
        idx, line = idx_of(fa), line_of(fa)

        lookup = self.swept and not self.filling and not self.replay_q
        line_ok = self.rd_idx_q == idx and self.tags.get(line) == tag_of(fa)
        hit = lookup and not self.conflict_q and line_ok
        win = fetch and sel
        fill_hold = bool(int(d.fill_hold.value))
        miss = win and lookup and not hit
        ctx = f"cycle {c}: fetch={fetch} sel={sel} addr={fa:06x} rd_idx={rd} inv={inv}/{inv_idx} hold={fill_hold:d}"
        assert int(d.hit.value) == hit, f"{ctx}: hit={int(d.hit.value)}, model {hit}"
        assert int(d.miss.value) == miss, f"{ctx}: miss={int(d.miss.value)}, model {miss}"
        assert int(d.mem_sel.value) == self.filling, f"{ctx}: mem_sel={int(d.mem_sel.value)}"
        if self.filling:
            # The line's first word, unchanged all through the fill
            assert int(d.mem_addr.value) == fa & ~(LINE_WORDS - 1), f"{ctx}: mem_addr {int(d.mem_addr.value):06x}"
        held = miss and fill_hold
        if held:
            # Never under a request in flight: fill_hold only falls during a fetch
            assert not self.filling, f"{ctx}: fill_hold rose under a fill"
            self.held += 1

        # The bridge: ready outside a fill, else when each word's response comes, garbage between
        done = self.filling and self.dues[self.cnt] == c
        mem_ready = done or not self.filling
        d.mem_ready.value = int(mem_ready)
        base = fa & ~(LINE_WORDS - 1)
        d.mem_rdata.value = self.word(base + self.cnt) if done else random.getrandbits(32)
        await Timer(1, unit="ns")

        ready = not win or hit
        assert int(d.instr_ready.value) == ready, f"{ctx}: instr_ready={int(d.instr_ready.value)}"
        if held:
            assert not ready, f"{ctx}: a held miss completed"
        if win and ready:
            got = int(d.rdata.value)
            assert got == self.word(fa), f"{ctx}: rdata {got:08x}, memory {self.word(fa):08x}"

        # The model's writes in this cycle (one tag write port)
        last = done and self.cnt == LINE_WORDS - 1
        tag_we, waddr = False, 0
        if not self.swept:
            if clear:
                tag_we, waddr = True, self.sweep_idx
                self.tags.pop(self.sweep_idx, None)
                self.sweep_idx += 1
                self.swept = self.sweep_idx == LINES
        elif last:
            tag_we, waddr = True, line
            self.tags[line] = tag_of(fa)
        elif inv:
            assert not self.filling, "the CPU stores nothing during a fill"
            tag_we, waddr = True, inv_idx
            self.tags.pop(inv_idx, None)
        self.rd_idx_q = rd
        self.conflict_q = tag_we and waddr == rd // LINE_WORDS
        self.replay_q = self.filling
        if done:
            self.cnt = (self.cnt + 1) % LINE_WORDS
        if last:
            self.filling = False
        elif miss and not fill_hold:
            self.filling = True
            self.dues = self.burst(c + 1)
            self.filled_by[line] = fa
            sample_miss_word(fa % LINE_WORDS)

        await FallingEdge(d.clk)
        self.cycle += 1
        return ready, hit

    async def sweep(self):
        for _ in range(LINES):
            await self.tick(clear=1)
        assert self.swept

    async def fetch(self, word, sel=True, inv=None, ahead=True, clear=1, hold=0):
        """The cycle before FETCH (EXECUTE, or a store's last MEM_WAIT cycle when inv is a
        word being stored), then FETCH until ready. ahead=False reads another word ahead (a
        path where pc_out isn't the next pc_reg), which must not hit. fill_hold is high in
        FETCH's first hold cycles, then low until the fetch completes (it only falls during a
        fetch)."""
        idx, line = idx_of(word), line_of(word)
        rd = idx if ahead else (idx + random.randrange(1, WORDS)) % WORDS
        inv_kind = "none"
        if inv is not None:
            self.mem[inv] = random.getrandbits(32)  # the store's new word
            inv_kind = "other_line" if line_of(inv) != line else "same_word" if idx_of(inv) == idx else "same_line"
        filled = self.tags.get(line)
        await self.tick(clear, rd, inv=inv is not None, inv_idx=line_of(inv or 0))
        swept = self.swept
        hit = None
        self.held = 0
        for n in range(LINES + 200):  # the sweep, or a fill
            ready, h = await self.tick(clear, idx, True, sel, word, hold=n < hold)
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
                if sel and swept:
                    sample_hold("held" if self.held else "free")
                if outcome == "hit":
                    sample_line_hit("same_word" if self.filled_by.get(line) == word else "other_word")
                return outcome
        raise AssertionError(f"fetch of {word:06x} never completed")


def pool(n_lines=6, n_tags=3):
    """Words on a few lines with a few tags each: hits, other words of a line, conflicts and
    evictions."""
    lines = random.sample(range(LINES), n_lines)
    tags = random.sample(range(1 << (21 - TAG_SHIFT)), n_tags)
    return [t << TAG_SHIFT | ln * LINE_WORDS | w for ln in lines for t in tags for w in random.sample(range(8), 3)]


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_directed(dut):
    """A fetch while the lines are swept waits; then miss, the line's other words hitting,
    eviction by another tag, a store to the line (at another word, at the very word, and to
    another line), a store to the next fetch's line in the cycle it is read (the board leaves
    that read undefined), a stale read-ahead, a fetch outside the window, and misses held by
    fill_hold (the data cache writing back the line) and a hit it doesn't hold"""
    b = Bench(dut, max_delay=3)
    await b.reset()
    # Before clear: no sweep yet, a window fetch waits without asking the memory
    for _ in range(3):
        ready, _ = await b.tick(0, 5, True, True, 5)
        assert not ready
    # During the sweep (clear high) it still waits, and completes once the sweep ends
    await b.fetch(0x1_2005)
    assert b.swept
    w = 0x0_4007  # the last word of a line
    assert await b.fetch(w) == "miss"
    assert await b.fetch(w) == "hit"
    for other in range(LINE_WORDS):  # each word of the line: all there, in their places
        assert await b.fetch(0x0_4000 + other) == "hit"
    assert await b.fetch(w + 1) == "miss", "the next line's first word"
    assert await b.fetch(w, inv=w - 3) == "miss", "a store to another word of the line drops it"
    assert await b.fetch(w - 3) == "hit", "and the fill has its new word"
    assert await b.fetch(w, inv=w + (1 << TAG_SHIFT)) == "miss", "a store to the line, another tag"
    assert await b.fetch(w, inv=0x0_0123) == "hit", "a store to another line leaves it"
    assert await b.fetch(w + (2 << TAG_SHIFT)) == "miss", "another tag on the line: replaces it"
    assert await b.fetch(w) == "miss"
    assert await b.fetch(w, inv=w) == "miss", "the store rewrites the very word: the fetch gets the new one"
    assert await b.fetch(w) == "hit"
    await b.fetch(w, ahead=False)  # a stale read-ahead misses
    await b.fetch(w, sel=False)  # outside the window
    await b.fetch(w)
    # fill_hold: the miss waits without asking the bridge (whose mem_ready is 1 meanwhile),
    # then goes to it and completes with the memory's word; a hit doesn't wait for it
    v = w + (3 << TAG_SHIFT)
    held_from = b.cycle
    assert await b.fetch(v, hold=4) == "miss"
    assert b.held == 4
    assert b.cycle - held_from > 1 + 4, "the held miss completed early"
    assert await b.fetch(v, hold=3) == "hit"
    assert b.held == 0
    assert await b.fetch(w, hold=1) == "miss"
    assert b.held == 1
    # A miss at each word of a line
    for k in range(LINE_WORDS):
        assert await b.fetch((0x8000 + LINE_WORDS * k) | k) == "miss"
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_controller_cycles(dut):
    """The costs the emulator copies: with the controller's burst read (its request taken in
    the cycle it rises), a miss keeps FETCH FILL = 49 cycles more than a hit, whichever word
    it fetches, and a miss whose probe writes the line back, PROBE_WB + FILL = 100"""
    b = Bench(dut, controller=True)
    await b.reset()
    await b.sweep()

    async def cycles(word, hold=0):
        await b.tick(1, idx_of(word))
        start = b.cycle
        for _ in range(FILL + PROBE_WB + 8):
            ready, _ = await b.tick(1, idx_of(word), True, True, word, hold=b.cycle - start < hold)
            if ready:
                return b.cycle - start
        raise AssertionError(f"fetch of {word:06x} never completed")

    hit = 1
    for word in (0x40, 0x87, 0xC3):
        assert await cycles(word) == hit + FILL
        assert await cycles(word) == hit
    assert await cycles(0x103, hold=PROBE_WB) == hit + PROBE_WB + FILL
    assert FILL == 49 and PROBE_WB + FILL == 100
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
        hold = random.choice((1, 2, 5)) if random.random() < 0.2 else 0
        await b.fetch(word, sel=random.random() < 0.9, inv=inv, ahead=random.random() < 0.9,
                      clear=random.randint(0, 1), hold=hold)  # fmt: skip
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_sweeps_valid_lines(dut):
    """rst over a cache whose every line is valid: the RAMs keep their contents, and the
    next sweep must clear every one of them, whatever order it takes, so each fetch misses.
    A reset cutting a fill short (its words partly replaced under the old tag) is the same:
    the sweep drops that tag."""
    b = Bench(dut, max_delay=2)
    await b.reset()
    await b.sweep()
    words = [random.getrandbits(21 - TAG_SHIFT) << TAG_SHIFT | ln * LINE_WORDS for ln in range(LINES)]
    for w in words:
        assert await b.fetch(w) == "miss"
    assert len(b.tags) == LINES
    # A fill under way, another tag on line 0, reset in the middle
    w = (tag_of(words[0]) ^ 1) << TAG_SHIFT
    for _ in range(4):
        await b.tick(1, idx_of(w), True, True, w)
    assert b.filling
    await b.reset()
    await b.sweep()
    for w in random.sample(words, LINES):
        assert await b.fetch(w) == "miss", f"{w:06x} hit after the sweep"
    fcov.export()
