# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_dcache: the write-back, write-allocate data cache in front of the wait-state window
(8-word lines, 64 of them). A CPU model makes loads and stores as unified_cpu does (the word
is read a cycle ahead at rd_idx, in EXECUTE, then MEM_WAIT is held until data_ready), Hack's
load and store back to back (AM=M+1), and between accesses the instruction cache's misses,
which probe the cache (probe_req held at probe_addr until probe_dirty falls, then for the
fetch's own miss). A memory model stands in for soc_xmem_bridge and the PSRAM controller:
one request at a time, taken when raised (or after a random delay, the controller's
req_ready low), its response a fixed (R/W, as the controller) or random number of cycles
later as the bridge's data_ready; mem_ready is 1 without a request, as the bridge's.

A cycle model of the cache (tags with valid/dirty, per-word byte masks, the FSM, the
read-ahead and its stale guard; a read colliding with a write reads as unknown, as on the
board, and the model fails if a decision ever depends on one) predicts every cycle's hit,
data_ready, probe_dirty, mem_sel and, while requesting, mem_we/mem_addr/mem_wdata/mem_wstrb.
Every word a load returns is checked against the CPU's view of the memory (its stores), and
every byte a write-back writes against it and against the bytes the CPU stored there: a
byte never stored must never reach the PSRAM (the W955 model counts an unmasked X byte as an
error). At the end of a test the dirty lines are evicted and the PSRAM compared with the
CPU's view. The bench reaches the DUT through a small adapter, so the stimulus and the model
also run in plain Python against the model itself."""

import random
from typing import Any

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

LINES = 64
NWORDS = LINES * 8
R = 35  # soc_pkg::XMEM_READ_LATENCY
W = 34  # soc_pkg::XMEM_WRITE_LATENCY
# Cycles over a hit (measured by test_cycle_costs; the emulator mirrors them)
CLEAN_MISS = 8 * (R + 1) + 2  # 290
DIRTY_MISS = 8 * (W + 1) + 8 * (R + 1) + 3  # 571
PROBE_WB = 8 * (W + 1) + 2  # 282 cycles of probe_dirty; the fetch's miss then takes R: 317

IDLE, WB, FILL = "IDLE", "WB", "FILL"
X: Any = object()  # a RAM read that collided with a write, or a word never written

KINDS = ("load", "store")
OUTCOMES = ("hit", "clean_miss", "dirty_miss", "sweep_wait", "outside")


# Per access: its kind and outcome, on a miss the victim, the word in its line, whether the
# read-ahead was at its word
@fcov.point("dcache.kind", KINDS, xf=lambda kind, outcome, victim, word, ahead: kind)
@fcov.point("dcache.outcome", OUTCOMES, xf=lambda kind, outcome, victim, word, ahead: outcome)
@fcov.cross("dcache.kind_x_outcome", ("dcache.kind", "dcache.outcome"))
@fcov.point(
    "dcache.victim", ("invalid", "clean", "dirty", "none"), xf=lambda kind, outcome, victim, word, ahead: victim
)
@fcov.point("dcache.word", tuple(range(8)), xf=lambda kind, outcome, victim, word, ahead: word)
@fcov.point("dcache.read_ahead", ("match", "mismatch"), xf=lambda kind, outcome, victim, word, ahead: ahead)
def sample(kind, outcome, victim, word, ahead):
    pass


# Per word written back: its strobes (the word's mask)
@fcov.point("dcache.wb_strobe", ("0000", "partial", "1111"), xf=lambda strobe: strobe)
def sample_wb(strobe):
    pass


# Per instruction-cache miss, once swept: what the probed line holds
@fcov.point("dcache.probe", ("none", "other_tag", "clean", "dirty_match"), xf=lambda probe: probe)
def sample_probe(probe):
    pass


# A store right after a load of the same word (Hack's MEM_WAIT then HACK_WB), by the load's
# outcome: the store hits in every case
@fcov.point("dcache.load_then_store", ("hit", "clean_miss", "dirty_miss"), xf=lambda load: load)
def sample_load_then_store(load):
    pass


def merge(old, new, strobe):
    out = old
    for i in range(4):
        if strobe >> i & 1:
            mask = 0xFF << (8 * i)
            out = out & ~mask | new & mask
    return out


class Inputs:
    """One cycle's inputs on the CPU and probe side."""

    def __init__(self, clear=1, rd_idx=0, req=False, sel=False, addr=0, we=False, wdata=0, wstrb=0, probe=False,
                 paddr=0):  # fmt: skip
        self.clear, self.rd_idx, self.req, self.sel, self.addr = clear, rd_idx, req, sel, addr
        self.we, self.wdata, self.wstrb, self.probe, self.paddr = we, wdata, wstrb, probe, paddr


class Model:
    """soc_dcache cycle for cycle: comb() gives the outputs of a cycle, clock() its edge."""

    def __init__(self):
        self.tags = [X] * LINES  # (valid, dirty, tag); the RAMs have no reset
        self.words = [X] * NWORDS  # (mask, data)
        self.reset()

    def reset(self):
        self.swept, self.sweep_idx, self.rd_idx_q, self.stale_q = False, 0, 0, False
        self.state, self.is_probe, self.primed, self.cnt, self.line_r, self.tag_r = IDLE, False, False, 0, 0, 0
        self.tag_q, self.word_q = X, X
        self.events = []  # what the edges did since the bench last looked: ("miss", victim), ("wb", strobe)

    def comb(self, i):
        idx, tag = i.addr & 0x1FF, i.addr >> 9
        win = i.req and i.sel
        lookup = win and self.swept and self.state == IDLE and not self.stale_q and self.rd_idx_q == idx
        hit = False
        if lookup:
            assert self.tag_q is not X, "a lookup used an unknown tag read"
            v, _, t = self.tag_q
            hit = bool(v) and t == tag
            assert not hit or self.word_q is not X and self.word_q[1] is not X, "a hit on an unknown word"
        pt = self.tags[i.paddr >> 3 & 0x3F]
        probe_dirty = False
        if self.swept:
            assert pt is not X, "the probe read an unknown tag after the sweep"
            probe_dirty = bool(pt[0] and pt[1]) and pt[2] == i.paddr >> 9
        o = {
            "lookup": lookup,
            "hit": hit,
            "miss": lookup and not hit,
            "data_ready": not win or hit,
            "probe_dirty": probe_dirty,
            "mem_sel": self.state == FILL or self.state == WB and self.primed,
            "mem_we": self.state == WB,
            "mem_addr": self.tag_r << 9 | self.line_r << 3 | self.cnt,
        }
        if o["mem_sel"] and o["mem_we"]:
            assert self.word_q is not X and self.word_q[1] is not X, "write-back of an unknown word"
            o["mem_wstrb"], o["mem_wdata"] = self.word_q
        return o

    def clock(self, i, o, mem_ready, mem_rdata):
        idx, line, tag = i.addr & 0x1FF, i.addr >> 3 & 0x3F, i.addr >> 9
        win = i.req and i.sel
        done = o["mem_sel"] and mem_ready
        last = done and self.cnt == 7
        cnt_next = (self.cnt + done) & 7
        ra = self.line_r << 3 | cnt_next if self.state == WB else i.rd_idx
        tag_w = word_w = None
        if not self.swept:
            if i.clear:
                tag_w = (self.sweep_idx, (0, 0, 0))
        elif o["hit"] and i.we:
            mask, data = self.word_q
            tag_w = (line, (1, 1, tag))
            word_w = (idx, (mask | i.wstrb, merge(data, i.wdata, i.wstrb)))
        elif self.state == FILL and done:
            word_w = (self.line_r << 3 | self.cnt, (0, mem_rdata))
            if last:
                tag_w = (self.line_r, (1, 0, self.tag_r))
        elif self.state == WB and last and self.is_probe:
            tag_w = (self.line_r, (1, 0, self.tag_r))
        # Reads at the edge; one colliding with the write is undefined (-no-rw-check)
        tag_q = X if tag_w and tag_w[0] == i.rd_idx >> 3 else self.tags[i.rd_idx >> 3]
        word_q = X if word_w and word_w[0] == ra else self.words[ra]
        if tag_w:
            self.tags[tag_w[0]] = tag_w[1]
        if word_w:
            self.words[word_w[0]] = word_w[1]
        if self.state == WB and done:
            m = o["mem_wstrb"]
            self.events.append(("wb", "0000" if m == 0 else "1111" if m == 0xF else "partial"))
        stale = (self.state != IDLE or tag_w is not None and tag_w[0] == i.rd_idx >> 3
                 or word_w is not None and word_w[0] == i.rd_idx)  # fmt: skip
        if not self.swept and i.clear:
            self.sweep_idx += 1
            self.swept = self.sweep_idx == LINES
        if self.state == IDLE:
            if o["miss"]:
                v = self.tag_q
                victim = "dirty" if v[0] and v[1] else "clean" if v[0] else "invalid"
                self.events.append(("miss", victim))
                self.line_r, self.is_probe = line, False
                if victim == "dirty":
                    self.state, self.tag_r = WB, v[2]
                else:
                    self.state, self.tag_r = FILL, tag
            elif self.swept and not win and i.probe and o["probe_dirty"]:
                self.state, self.line_r, self.tag_r, self.is_probe = WB, i.paddr >> 3 & 0x3F, i.paddr >> 9, True
                self.events.append(("probe_wb", None))
        elif self.state == WB:
            self.primed = not last
            if last:
                if self.is_probe:
                    self.state = IDLE
                else:
                    self.state, self.tag_r = FILL, tag
        elif last:
            self.state = IDLE
        self.cnt = cnt_next
        self.rd_idx_q, self.stale_q, self.tag_q, self.word_q = i.rd_idx, stale, tag_q, word_q


class Memory:
    """soc_xmem_bridge's data side and the PSRAM controller: one request at a time."""

    def __init__(self, fixed):
        self.fixed = fixed  # True: taken when raised, the response R/W cycles later
        self.psram = {}  # PSRAM word -> value
        self.reset()

    def reset(self):
        self.busy = False
        self.due = None
        self.rsp = 0
        self.wait = None  # cycles until req_ready (random mode)
        self.raised = None  # the fields of a request not yet taken

    def word(self, w):
        if w not in self.psram:
            self.psram[w] = random.getrandbits(32)
        return self.psram[w]

    def cycle(self, c, o, on_write):
        """Returns (mem_ready, mem_rdata) for this cycle."""
        if not o["mem_sel"]:
            assert not self.busy and self.raised is None, f"cycle {c}: mem_sel dropped under a request"
            return True, random.getrandbits(32)
        if self.busy:
            if c == self.due:
                self.busy = False
                return True, self.rsp
            return False, random.getrandbits(32)
        fields = (o["mem_we"], o["mem_addr"], o.get("mem_wdata"), o.get("mem_wstrb"))
        if self.raised is None:
            self.raised = fields
            self.wait = 0 if self.fixed else random.choice((0, 0, 0, 1, 2))
        assert fields == self.raised, f"cycle {c}: request fields changed before it was taken"
        if self.wait:
            self.wait -= 1
            return False, random.getrandbits(32)
        self.raised = None
        we, addr, wdata, wstrb = fields
        if we:
            on_write(addr, wdata, wstrb)
            lat = W if self.fixed else random.randint(1, 6)
        else:
            self.rsp = self.word(addr)
            lat = R if self.fixed else random.randint(1, 6)
        self.busy, self.due = True, c + lat
        return False, random.getrandbits(32)


class CocotbPort:
    """The DUT's ports in the simulator."""

    def __init__(self, dut):
        self.dut = dut
        self.clocked = False

    async def start(self):
        if not self.clocked:
            cocotb.start_soon(Clock(self.dut.clk, 20, unit="ns").start())
            self.clocked = True
        await FallingEdge(self.dut.clk)

    def set_rst(self, v):
        self.dut.rst.value = v

    def drive(self, i):
        d = self.dut
        d.clear.value = i.clear
        d.rd_idx.value = i.rd_idx
        d.data_req.value = int(i.req)
        d.data_sel.value = int(i.sel)
        d.data_addr.value = i.addr
        d.data_we.value = int(i.we)
        d.data_wdata.value = i.wdata
        d.data_wstrb.value = i.wstrb
        d.probe_req.value = int(i.probe)
        d.probe_addr.value = i.paddr

    def drive_mem(self, ready, rdata):
        self.dut.mem_ready.value = int(ready)
        self.dut.mem_rdata.value = rdata

    async def settle(self):
        await Timer(1, unit="ns")

    def get(self, name):
        return int(getattr(self.dut, name).value)

    async def edge(self):
        await FallingEdge(self.dut.clk)


class ModelPort:
    """The same ports on a second copy of the model: the plain-Python dry run."""

    def __init__(self):
        self.m = Model()
        self.i = Inputs()
        self.ready, self.rdata = True, 0
        self.rst = 1

    async def start(self):
        pass

    def set_rst(self, v):
        self.rst = v
        if not v:
            self.m.reset()

    def drive(self, i):
        self.i = i

    def drive_mem(self, ready, rdata):
        self.ready, self.rdata = ready, rdata

    async def settle(self):
        pass

    def get(self, name):
        o = self.m.comb(self.i)
        if name == "rdata":
            return self.m.word_q[1]
        return int(o[name])

    async def edge(self):
        if self.rst:
            self.m.clock(self.i, self.m.comb(self.i), self.ready, self.rdata)
        self.m.events.clear()


class Bench:
    def __init__(self, port, fixed=False):
        self.port = port
        self.model = Model()
        self.mem = Memory(fixed)
        self.view = {}  # the CPU's view of the memory: PSRAM word -> value
        self.stored = {}  # PSRAM word -> the strobes the CPU ever stored there
        self.cycle = 0
        self.events = []

    def value(self, w):
        if w not in self.view:
            self.view[w] = self.mem.word(w)
        return self.view[w]

    async def reset(self):
        """rst for a cycle (soc_top's rst_n, which also resets the controller): the registers
        reset, the RAMs keep their lines until the sweep, which drops the dirty ones: their
        stores never reach the PSRAM."""
        p = self.port
        await p.start()
        p.set_rst(0)
        p.drive(self.idle_inputs(0))
        p.drive_mem(1, 0)
        self.model.reset()
        self.mem.reset()
        self.view = {}  # the stores still in dirty lines are lost
        await p.edge()
        p.set_rst(1)

    def on_write(self, addr, wdata, wstrb):
        stored = self.stored.get(addr, 0)
        assert wstrb & ~stored == 0, f"write-back of {addr:06x}: strobes {wstrb:04b}, stored only {stored:04b}"
        view = self.value(addr)
        assert merge(view, wdata, wstrb) == view, f"write-back of {addr:06x}: {wdata:08x}/{wstrb:04b}, CPU {view:08x}"
        self.mem.psram[addr] = merge(self.mem.word(addr), wdata, wstrb)

    def idle_inputs(self, clear=1, probe=False, paddr=None):
        """A cycle without a data access: the CPU in FETCH or EXECUTE; data_sel and the fields
        still decode whatever data_addr is."""
        return Inputs(clear, random.randrange(NWORDS), False, bool(random.getrandbits(1)), random.getrandbits(21),
                      bool(random.getrandbits(1)), random.getrandbits(32), random.randrange(16), probe,
                      random.getrandbits(21) if paddr is None else paddr)  # fmt: skip

    async def tick(self, i):
        """One cycle against the model; returns the model's outputs (data_ready, hit)."""
        p, m, c = self.port, self.model, self.cycle
        p.drive(i)
        await p.settle()
        o = m.comb(i)
        ctx = (f"cycle {c}: req={i.req:d} sel={i.sel:d} we={i.we:d} addr={i.addr:06x} rd_idx={i.rd_idx:03x} "
               f"wstrb={i.wstrb:04b} probe={i.probe:d}/{i.paddr:06x} state={m.state} cnt={m.cnt}")  # fmt: skip
        for name in ("hit", "data_ready", "probe_dirty", "mem_sel"):
            got = p.get(name)
            assert got == o[name], f"{ctx}: {name}={got}, model {o[name]:d}"
        if o["mem_sel"]:
            for name in ("mem_we", "mem_addr") + (("mem_wdata", "mem_wstrb") if o["mem_we"] else ()):
                got = p.get(name)
                assert got == o[name], f"{ctx}: {name}={got:x}, model {o[name]:x}"
        ready, rdata = self.mem.cycle(c, o, self.on_write)
        p.drive_mem(ready, rdata)
        await p.settle()
        if i.req and i.sel and o["hit"]:
            if i.we:
                self.view[i.addr] = merge(self.value(i.addr), i.wdata, i.wstrb)
                self.stored[i.addr] = self.stored.get(i.addr, 0) | i.wstrb
            else:
                got, want = p.get("rdata"), self.value(i.addr)
                assert got == want, f"{ctx}: rdata {got:08x}, CPU's view {want:08x}"
        if m.state == FILL and o["mem_sel"] and ready:
            assert rdata == self.mem.word(o["mem_addr"])
        m.clock(i, o, ready, rdata)
        for kind, arg in m.events:
            if kind == "wb":
                sample_wb(arg)
        self.events += m.events
        m.events = []
        await p.edge()
        self.cycle += 1
        return o

    async def mem_wait(self, i, cap=2000):
        """MEM_WAIT until data_ready; returns its cycles."""
        for n in range(1, cap + 1):
            o = await self.tick(i)
            if o["data_ready"]:
                return n
        raise AssertionError(f"access to {i.addr:06x} never completed")

    def outcome(self, sel, swept):
        if not sel:
            return "outside", "none"
        if not swept:
            return "sweep_wait", "none"
        misses = [a for k, a in self.events if k == "miss"]
        if not misses:
            return "hit", "none"
        assert len(misses) == 1, misses
        return ("dirty_miss" if misses[0] == "dirty" else "clean_miss"), misses[0]

    async def access(self, kind, word, sel=True, wstrb=0xF, ahead=True, clear=1):
        """EXECUTE (the word read ahead at rd_idx, or at another word), then MEM_WAIT until
        ready. Returns (outcome, MEM_WAIT cycles)."""
        idx = word & 0x1FF
        rd = idx if ahead else (idx + random.randrange(1, NWORDS)) % NWORDS
        i = Inputs(clear, idx, True, sel, word, kind == "store", random.getrandbits(32), wstrb)
        e = self.idle_inputs(clear)
        e.rd_idx = rd
        await self.tick(e)
        swept = self.model.swept
        self.events = []
        n = await self.mem_wait(i)
        outcome, victim = self.outcome(sel, swept)
        if sel and swept:
            sample(kind, outcome, victim, word & 7, "match" if ahead else "mismatch")
        elif not sel:
            sample(kind, outcome, "none", word & 7, "match" if ahead else "mismatch")
        else:
            sample(kind, outcome, "none", word & 7, "match")
        return outcome, n

    async def load_then_store(self, word, clear=1):
        """A Hack instruction that reads M and writes it (AM=M+1): EXECUTE, MEM_WAIT's load
        until ready, then from the next cycle HACK_WB's full-word store until ready, rd_idx at
        the word throughout. Returns the load's outcome and the store's cycles."""
        idx = word & 0x1FF
        await self.tick(Inputs(clear, idx))
        assert self.model.swept, "load_then_store before the sweep"
        self.events = []
        await self.mem_wait(Inputs(clear, idx, True, True, word, False, 0, 0xF))
        load, _ = self.outcome(True, True)
        self.events = []
        n = await self.mem_wait(Inputs(clear, idx, True, True, word, True, random.getrandbits(32), 0xF))
        assert n == 1 and not self.events, f"AM=M+1 at {word:06x}: the store took {n} cycles"
        sample_load_then_store(load)
        return load, n

    def probe_kind(self, paddr):
        t = self.model.tags[paddr >> 3 & 0x3F]
        if not t[0]:
            return "none"
        if t[2] != paddr >> 9:
            return "other_tag"
        return "dirty_match" if t[1] else "clean"

    async def fetch_miss(self, paddr, own=None, clear=1, cap=1000):
        """An instruction-cache miss at paddr: probe_req held while probe_dirty is high, then
        for the miss's own cycles at the bridge (own; random by default). Returns the cycles
        probe_dirty was high."""
        kind = self.probe_kind(paddr) if self.model.swept else None
        if kind:
            sample_probe(kind)
        own = random.randint(1, 4) if own is None else own
        held = 0
        for _ in range(cap):
            o = await self.tick(self.idle_inputs(clear, True, paddr))
            if not o["probe_dirty"]:
                break
            held += 1
        else:
            raise AssertionError(f"probe at {paddr:06x} never cleared")
        for _ in range(own - 1):
            await self.tick(self.idle_inputs(clear, True, paddr))
        assert (kind == "dirty_match") == (held > 0), f"probe at {paddr:06x}: {kind}, held {held}"
        return held

    async def idle(self, n=None, clear=1):
        for _ in range(random.randint(0, 2) if n is None else n):
            await self.tick(self.idle_inputs(clear))

    async def flush(self):
        """Evict every dirty line (a load of another tag at its line), then compare the
        PSRAM with the CPU's view."""
        for ln in range(LINES):
            t = self.model.tags[ln]
            if t[0] and t[1]:
                await self.access("load", ((t[2] + 1) & 0xFFF) << 9 | ln << 3)
        for w, v in self.view.items():
            assert self.mem.word(w) == v, f"PSRAM {w:06x} = {self.mem.word(w):08x}, CPU {v:08x}"


def pool(n_lines=6, n_tags=3):
    """Line bases (tag, line) on a few lines with a few tags each: hits, conflicts and
    evictions; an access picks a word of one."""
    lines = random.sample(range(LINES), n_lines)
    tags = random.sample(range(1 << 12), n_tags)
    return [t << 9 | ln << 3 for ln in lines for t in tags]


async def directed(b):
    """Accesses while the lines are swept wait; then misses with an invalid, a clean and a
    dirty victim (loads and stores, partial ones too), hits, a stale read-ahead, accesses
    outside the window, write-backs of untouched/partial/full words, the probe on each kind
    of line, and Hack's load and store after each outcome"""
    await b.reset()
    for _ in range(3):  # before clear: waits without asking the memory
        o = await b.tick(Inputs(0, 5, True, True, 5))
        assert not o["data_ready"]
    assert (await b.access("store", 0x1_2005, wstrb=0x2))[0] == "sweep_wait"
    assert (await b.access("load", 0x1_2006))[0] == "hit"
    v = 0x0_4038  # line 7
    assert (await b.access("load", v))[0] == "clean_miss"  # invalid victim
    assert (await b.access("load", v | 1))[0] == "hit"
    assert (await b.access("store", v | 2, wstrb=0b0010))[0] == "hit"  # partial store hit
    assert (await b.access("store", v | 3, wstrb=0xF))[0] == "hit"  # full store hit
    assert (await b.access("load", v | 2, ahead=False))[0] == "hit"  # a stale read-ahead
    assert (await b.access("store", v | 2, wstrb=0b0100, ahead=False))[0] == "hit"
    v2 = v + (1 << 9)  # line 7, another tag
    assert (await b.access("store", v2 | 5, wstrb=0b1100))[0] == "dirty_miss"  # strobes 0000, partial, 1111
    assert (await b.access("load", v | 2))[0] == "dirty_miss"
    assert (await b.access("load", v2 | 5))[0] == "clean_miss"  # clean victim (written back)
    assert (await b.access("load", v | 7, sel=False))[0] == "outside"
    assert (await b.access("store", v | 7, sel=False, wstrb=1))[0] == "outside"
    # The probe on an invalid line, another tag, a clean line, a dirty line of another tag
    # and a dirty match (written back, stays valid and clean: the load hits)
    await b.fetch_miss(0x3_0000 | 30 << 3)
    await b.fetch_miss(v | 1)
    await b.fetch_miss(v2 | 1)
    assert (await b.access("store", v2 | 4, wstrb=0x1))[0] == "hit"
    await b.fetch_miss(v | 4)  # other_tag, the line dirty
    assert await b.fetch_miss(v2 | 6) > 0
    assert b.probe_kind(v2) == "clean"
    assert (await b.access("load", v2 | 4))[0] == "hit"
    # Hack's load then store: after a hit, a clean miss and a dirty miss
    u = 0x2_0123
    await b.load_then_store(u)  # clean miss
    assert (await b.load_then_store(u))[0] == "hit"
    assert (await b.load_then_store(u + (1 << 9)))[0] == "dirty_miss"
    # Every word of a line, a store miss of every kind
    for k in range(8):
        await b.access("load", 0x5_0000 | 9 << 3 | k)
    assert (await b.access("store", 0x5_1000 | 9 << 3 | 3, wstrb=0x8))[0] == "clean_miss"
    await b.flush()


async def random_run(b, steps):
    """Random loads and stores (random strobes) over a few lines and tags, stale read-aheads,
    accesses outside the window, Hack's back-to-back load and store, and instruction misses
    probing the lines (often a word just stored)"""
    await b.reset()
    bases = pool()
    await b.access("store", random.choice(bases) | random.randrange(8))  # waits for the sweep
    last = None
    for _ in range(steps):
        r = random.random()
        word = random.choice(bases) | random.randrange(8)
        if r < 0.08:
            await b.load_then_store(word, clear=random.randint(0, 1))
        elif r < 0.2:
            paddr = last if last is not None and random.random() < 0.5 else word
            await b.fetch_miss(paddr, clear=random.randint(0, 1))
        else:
            kind = random.choice(KINDS)
            wstrb = random.choice((0xF, 0xF, 0x1, 0x2, 0x4, 0x8, 0x3, 0xC, random.randrange(1, 16)))
            await b.access(kind, word, sel=random.random() < 0.9, wstrb=wstrb, ahead=random.random() < 0.9,
                           clear=random.randint(0, 1))  # fmt: skip
            if kind == "store":
                last = word
        await b.idle()
    await b.flush()


async def reset_drops_dirty(b):
    """rst over a cache whose every line is dirty: the sweep clears every one, whatever order
    it takes, so each load misses with an invalid victim and reads the PSRAM, which never got
    the stores"""
    await b.reset()
    words = [random.getrandbits(12) << 9 | ln << 3 | random.randrange(8) for ln in range(LINES)]
    for w in words:
        assert (await b.access("store", w, wstrb=0x5))[0] in ("clean_miss", "sweep_wait")
    assert all(t[0] and t[1] for t in b.model.tags)
    await b.reset()
    for w in random.sample(words, LINES):
        outcome, _ = await b.access("load", w)
        assert outcome in ("clean_miss", "sweep_wait"), f"{w:06x}: {outcome} after the sweep"
    assert not any(k == "wb" for k, _ in b.events)
    await b.flush()


async def cycle_costs(b):
    """The cycle costs with the controller's fixed latencies (R = 35, W = 34): a hit
    completes in its first MEM_WAIT cycle; misses, the probe's write-back and a stale
    read-ahead cost what the header (and the emulator) says"""
    await b.reset()
    await b.access("load", 0)  # waits for the sweep
    w = 0x0_1234
    assert await b.access("load", w) == ("clean_miss", 1 + CLEAN_MISS)  # invalid victim
    assert await b.access("load", w) == ("hit", 1)
    assert await b.access("store", w, wstrb=0x3) == ("hit", 1)
    assert await b.access("load", w, ahead=False) == ("hit", 2)
    assert await b.access("load", w + (1 << 9)) == ("dirty_miss", 1 + DIRTY_MISS)
    assert await b.access("load", w) == ("clean_miss", 1 + CLEAN_MISS)  # clean victim
    assert await b.access("store", w + (2 << 9), wstrb=0x1) == ("clean_miss", 1 + CLEAN_MISS)
    assert await b.load_then_store(w + (3 << 9)) == ("dirty_miss", 1)
    # The probe: none without a dirty match; a dirty match holds the fetch PROBE_WB cycles,
    # after which its own miss takes R (317 in all)
    assert await b.fetch_miss(w + (4 << 9), own=R) == 0
    assert await b.fetch_miss(w + (3 << 9), own=R) == PROBE_WB
    assert await b.access("load", w + (3 << 9)) == ("hit", 1)
    await b.flush()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_directed(dut):
    """Directed cases with random latencies (the docstring of directed())"""
    await directed(Bench(CocotbPort(dut)))
    fcov.export()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_cycle_costs(dut):
    """The cycle costs with the controller's fixed latencies"""
    await cycle_costs(Bench(CocotbPort(dut), fixed=True))
    fcov.export()


@cocotb.test(timeout_time=40, timeout_unit="ms")
async def test_random(dut):
    """Random accesses and probes against the model (the docstring of random_run())"""
    await random_run(Bench(CocotbPort(dut)), 1200)
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_drops_dirty(dut):
    """rst drops the dirty lines (the docstring of reset_drops_dirty())"""
    await reset_drops_dirty(Bench(CocotbPort(dut)))
    fcov.export()
