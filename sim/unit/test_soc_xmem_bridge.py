# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_xmem_bridge: an access the caches pass on (fetch_sel / data_sel, their mem_sel)
becomes exactly one request of the memory controller's contract, held until accepted: a
fetch a single word (req_len 0), a data access a whole line (req_len 7). A fetch completes
(instr_ready) in its response's cycle; a data load gets data_ready in each of its 8 words'
cycles, a data store in its one response's, with the controller's wdata_next passed on
(data_wnext) and the next word's fields passed through; only rsp_last ends the request. Accesses the caches don't
pass on (out of the window, or hits) complete at once and request nothing; nothing is
requested while the CPU is held in soft reset. Both sides may hold an access at once (the
data cache's write-back while the instruction cache holds its miss): the data side's
request goes first, the other side waits, and a response completes only the side whose
request it answers. A CPU-and-caches model holds each access until it completes, a memory
model behind the bridge accepts and answers with random delays (the contract's lower
bounds), or in the PSRAM controller's cycles (test_controller_cycles), and stays not ready
for a while after a burst read's last word, as the controller; every output is checked in
every cycle."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

KINDS = ("fetch", "load", "store")
OWNER = ("fetch_done", "data_done", "other_side_waits")
RESPONSE = ("fetch", "read_word", "read_last", "write_last")
LINE = 8


# Each access kind inside and outside the window, and whether the memory kept the request
# waiting (req_ready low) before taking it
@fcov.point("xbridge.kind", KINDS, xf=lambda kind, window, waited: kind)
@fcov.point("xbridge.window", ("in", "out"), xf=lambda kind, window, waited: window)
@fcov.cross("xbridge.kind_x_window", ("xbridge.kind", "xbridge.window"))
@fcov.point("xbridge.accept", ("at_once", "after_wait"), xf=lambda kind, window, waited: waited)
def sample(kind, window, waited):
    pass


# Both selects raised: a response completing the fetch / the data access while the other
# side's select is high, and a cycle where one side waits on the other's request in flight
@fcov.point("xbridge.owner", OWNER, xf=lambda owner: owner)
def sample_owner(owner):
    pass


# What the memory answered: a fetch's word, a data read's word and its last, a write's one
# response; and whether a burst write passed its wdata_next on, and a request that waited for
# the controller to recover from a burst read
@fcov.point("xbridge.response", RESPONSE, xf=lambda response: response)
def sample_response(response):
    pass


@fcov.point("xbridge.wnext", tuple(range(1, LINE)), xf=lambda word: word)
def sample_wnext(word):
    pass


@fcov.point("xbridge.after_read", ("waits", "ready"), xf=lambda after: after)
def sample_after_read(after):
    pass


class Memory:
    """Behind the bridge: takes a request when ready (randomly low), answers after random
    delays (controller=True: in the controller's cycles after the accepting one). A single
    word: one rsp_valid with rsp_last. A burst read: 8 rsp_valid, rsp_last on the last,
    not ready until a while later. A burst write: wdata_next 7 times, each word in the next
    cycle, then one rsp_valid with rsp_last, ready in its cycle."""

    def __init__(self, p_ready, max_delay, min_delay=1, controller=False):
        self.p_ready = p_ready
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.controller = controller
        self.rsps = {}  # cycle -> whether it is the last response
        self.wnext = {}  # cycle -> the word its next cycle takes
        self.ready_at = 0
        self.ready = True
        self.kind = None

    def delay(self):
        return random.randint(self.min_delay, self.max_delay)

    def outputs(self, cycle):
        """req_ready, rsp_valid, rsp_last, rsp_rdata, wdata_next for this cycle. The word
        lines are garbage outside a response too: rdata follows them."""
        if not self.rsps:
            self.ready = random.random() < self.p_ready
        rsp = cycle in self.rsps
        last = rsp and self.rsps[cycle]
        idle = not self.rsps or last
        return (
            int(self.ready and idle and cycle >= self.ready_at),
            int(rsp),
            int(last),
            random.getrandbits(32),
            int(cycle in self.wnext),
        )

    def accept(self, cycle, kind):
        self.kind = kind
        self.wnext = {}
        if kind == "fetch":
            at = cycle + (35 if self.controller else self.delay())
            self.rsps, self.ready_at = {at: True}, at
        elif kind == "store":
            t = cycle
            for k in range(1, LINE):
                t = cycle + 28 + 2 * k if self.controller else t + self.delay()
                self.wnext[t] = k
            at = cycle + 48 if self.controller else t + 1 + self.delay()
            self.rsps, self.ready_at = {at: True}, at
        else:
            ats = []
            t = cycle
            for k in range(LINE):
                t = cycle + 32 + 2 * k if self.controller else t + self.delay()
                ats.append(t)
            self.rsps = {a: a == t for a in ats}
            self.ready_at = cycle + 49 if self.controller else t + random.randint(1, 4)

    def clock(self, cycle, accepted, kind):
        self.rsps.pop(cycle, None)
        if accepted:
            self.accept(cycle, kind)


class Access:
    """One access a side holds until it completes. hold (fetch only) is the instruction
    cache's fill_hold: its select is low while the data side owns the bridge."""

    def __init__(self, kind, window):
        self.kind, self.window = kind, window
        self.addr = random.getrandbits(21)
        self.wdata, self.wstrb = random.getrandbits(32), random.randrange(16)
        self.waited = False  # the memory kept this access's request waiting
        self.hold = False
        self.words = LINE if kind == "load" else 1  # the readies its request still owes
        self.cycles = 0

    @property
    def sel(self):
        return self.window and not self.hold

    def finished(self, ready):
        """One cycle's ready: the access completes when its request's last word does; out of
        the window, at once."""
        if not ready:
            return False
        if self.window:
            self.words -= 1
        return self.words <= 0 or not self.window

    def done(self):
        sample(self.kind, "in" if self.window else "out", "after_wait" if self.waited else "at_once")


class Bench:
    def __init__(self, dut, p_ready=0.7, max_delay=5, min_delay=1, controller=False):
        self.dut = dut
        self.mem = Memory(p_ready, max_delay, min_delay, controller)
        self.busy = False  # the bridge's in-flight request, as the contract implies
        self.busy_data = False  # ... and whether it is the data side's
        self.presenting = None  # the side whose request was refused last cycle (held)
        self.cycle = 0

    async def reset(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
        await FallingEdge(dut.clk)
        dut.rst.value = 0
        self.drive(0, None, None)
        dut.req_ready.value = 1
        dut.rsp_valid.value = 0
        dut.rsp_last.value = 0
        dut.rsp_rdata.value = 0
        dut.wdata_next.value = 0
        await FallingEdge(dut.clk)
        dut.rst.value = 1

    def drive(self, soft_rst, fetch, data):
        """The caches' side for this cycle: fetch / data None is a cycle without FETCH /
        MEM_WAIT; an Access out of the window one the caches don't pass on. Fields of a
        side without a select are random."""
        d = self.dut
        d.soft_rst.value = soft_rst
        d.fetch_sel.value = int(fetch is not None and fetch.sel)
        d.fetch_addr.value = fetch.addr if fetch is not None else random.getrandbits(21)
        d.data_sel.value = int(data is not None and data.sel)
        d.data_addr.value = data.addr if data is not None else random.getrandbits(21)
        d.data_we.value = int(data.kind == "store") if data is not None else random.getrandbits(1)
        d.data_wdata.value = data.wdata if data is not None else random.getrandbits(32)
        d.data_wstrb.value = data.wstrb if data is not None else random.randrange(16)

    async def tick(self, soft_rst=0, fetch=None, data=None):
        """One cycle; returns (instr_ready, data_ready, whether the memory took a request)."""
        d, c = self.dut, self.cycle
        self.drive(soft_rst, fetch, data)
        req_ready, rsp_valid, rsp_last, rsp_rdata, wdata_next = self.mem.outputs(c)
        d.req_ready.value, d.rsp_valid.value, d.rsp_last.value = req_ready, rsp_valid, rsp_last
        d.rsp_rdata.value, d.wdata_next.value = rsp_rdata, wdata_next
        # A burst write: the word the memory takes next cycle is on the fields from then on
        if data is not None and c - 1 in self.mem.wnext:
            data.wdata, data.wstrb = random.getrandbits(32), random.randrange(16)
            d.data_wdata.value, d.data_wstrb.value = data.wdata, data.wstrb
        await Timer(1, unit="ns")

        fsel = fetch is not None and fetch.sel
        dsel = data is not None and data.sel
        want_valid = (fsel or dsel) and not self.busy and not soft_rst
        done = self.busy and rsp_valid
        ctx = (
            f"cycle {c}: fetch_sel={int(fsel)} data_sel={int(dsel)}"
            f" ({data.kind if data is not None else '-'}), busy={self.busy}, busy_data={self.busy_data},"
            f" soft_rst={soft_rst}"
        )
        assert int(d.req_valid.value) == want_valid, f"{ctx}: req_valid={int(d.req_valid.value)}"
        if want_valid:
            req = data if dsel else fetch  # the data side's request goes first
            store = dsel and data.kind == "store"
            assert int(d.req_we.value) == store, f"{ctx}: req_we"
            assert int(d.req_addr.value) == req.addr, f"{ctx}: req_addr {int(d.req_addr.value):06x}"
            want_len = LINE - 1 if dsel else 0
            assert int(d.req_len.value) == want_len, f"{ctx}: req_len {int(d.req_len.value)}"
        assert int(d.data_wnext.value) == wdata_next, f"{ctx}: data_wnext"
        if wdata_next:
            sample_wnext(self.mem.wnext[c])
        if dsel:
            assert (int(d.req_wdata.value), int(d.req_wstrb.value)) == (data.wdata, data.wstrb), f"{ctx}: write data"
        if rsp_valid:
            kind = self.mem.kind
            assert self.busy, f"{ctx}: a response without a request"
            sample_response(
                "fetch"
                if kind == "fetch"
                else "write_last"
                if kind == "store"
                else "read_last"
                if rsp_last
                else "read_word"
            )
        want_instr = not fsel or (done and not self.busy_data)
        want_data = not dsel or (done and self.busy_data)
        assert int(d.instr_ready.value) == want_instr, f"{ctx}: instr_ready={int(d.instr_ready.value)}"
        assert int(d.data_ready.value) == want_data, f"{ctx}: data_ready={int(d.data_ready.value)}"
        assert int(d.rdata.value) == rsp_rdata, f"{ctx}: rdata"

        if fsel and dsel:
            if done:
                sample_owner("data_done" if self.busy_data else "fetch_done")
            elif self.busy:
                sample_owner("other_side_waits")

        accepted = want_valid and req_ready
        if want_valid and self.mem.kind == "load":
            sample_after_read("waits" if c < self.mem.ready_at else "ready")
        if want_valid and not req_ready:
            (data if dsel else fetch).waited = True
        self.presenting = ("data" if dsel else "fetch") if want_valid and not req_ready else None
        req_kind = None
        if accepted:
            req_kind = data.kind if dsel else "fetch"
        self.mem.clock(c, accepted, req_kind)
        if accepted:
            self.busy, self.busy_data = True, dsel
        elif rsp_last:
            self.busy = False
        await FallingEdge(d.clk)
        self.cycle += 1
        return want_instr, want_data, accepted

    async def access(self, kind, window, gap=0):
        """Idle `gap` cycles, then hold one access until it completes."""
        for _ in range(gap):
            await self.tick()
        a = Access(kind, window)
        for _ in range(400):
            if kind == "fetch":
                ready, _, _ = await self.tick(0, a, None)
            else:
                _, ready, _ = await self.tick(0, None, a)
            if a.finished(ready):
                a.done()
                return
        raise AssertionError(f"{kind} at word {a.addr:06x} never completed")

    def fill_hold(self):
        """The instruction cache's fill_hold while the data side owns the bridge: dropped in
        the data's last response's cycle, so the fetch's select rises in it."""
        return self.busy and self.busy_data and not self.mem.rsps.get(self.cycle, False)

    async def drain(self, cap=100):
        """Idle until the memory is ready again for a new request (no refused fetch for the
        data side to change under)."""
        for _ in range(cap):
            if self.cycle >= self.mem.ready_at and not self.mem.rsps:
                return
            await self.tick()
        raise AssertionError("the memory never got ready")

    async def both(self, first, second, lead, hold=False, max_cycles=400):
        """first raises its select, second (the other side) `lead` cycles later; each held
        until it completes. hold: the fetch's select follows fill_hold(). Returns the
        kinds in their order of completion."""
        await self.drain()
        sides: dict[str, Access | None] = {"fetch": None, "data": None}
        side = lambda a: "fetch" if a.kind == "fetch" else "data"  # noqa: E731
        sides[side(first)] = first
        order = []
        for step in range(max_cycles):
            if step == lead:
                # Outside the contract: the fetch's refused request would change its fields
                assert not (side(second) == "data" and self.presenting == "fetch")
                sides[side(second)] = second
            f, dt = sides["fetch"], sides["data"]
            if f is not None and hold:
                f.hold = self.fill_hold()
            ir, dr, _ = await self.tick(0, f, dt)
            if f is not None and not f.hold and f.finished(ir):
                f.done()
                order.append(f.kind)
                sides["fetch"] = None
            if dt is not None and dt.finished(dr):
                dt.done()
                order.append(dt.kind)
                sides["data"] = None
            if step >= lead and sides == {"fetch": None, "data": None}:
                return order
        raise AssertionError(f"{first.kind} and {second.kind} never both completed")

    async def run_sides(self, steps, p_hold=0.5):
        """Both sides independently: each holds a random access (in or out of the window)
        until it completes, then idles a random gap. The fetch's select may drop while the
        data side owns the bridge (fill_hold); the data side never raises its select while
        the fetch's request is refused (the controller's fields must hold until accepted).
        steps caps the run, a per-access cap catches an access that never completes."""
        sides: dict[str, Access | None] = {"fetch": None, "data": None}
        gaps = {"fetch": 0, "data": 0}
        for _ in range(steps):
            for s in sides:
                if sides[s] is None:
                    if gaps[s]:
                        gaps[s] -= 1
                    elif not (s == "data" and self.presenting == "fetch"):
                        kind = "fetch" if s == "fetch" else random.choice(("load", "store"))
                        sides[s] = Access(kind, random.random() < 0.7)
            f, dt = sides["fetch"], sides["data"]
            if f is not None:
                f.hold = self.busy and self.busy_data and random.random() < p_hold
            ir, dr, _ = await self.tick(0, f, dt)
            for s, a, ready in (("fetch", f, ir and not (f is not None and f.hold)), ("data", dt, dr)):
                if a is None:
                    continue
                a.cycles += 1
                if a.finished(ready):
                    a.done()
                    sides[s] = None
                    # A data access right after the last one only while no fetch waits: the
                    # data side's priority would starve it (the CPU never does this)
                    gaps[s] = random.choice((0, 0, 1, 2, 3) if s == "fetch" or sides["fetch"] is None else (1, 2, 3))
                elif a.cycles > 600:
                    raise AssertionError(f"{a.kind} at word {a.addr:06x} never completed")


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_directed(dut):
    """Each kind in and out of the window with the memory always ready, then with it
    refusing for a while (the PSRAM before init_done)"""
    b = Bench(dut, p_ready=1.0, max_delay=3)
    await b.reset()
    for kind in KINDS:
        for window in (True, False):
            await b.access(kind, window, gap=1)
    b.mem.p_ready = 0.1
    for kind in KINDS:
        await b.access(kind, True, gap=2)
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_controller_cycles(dut):
    """The controller's cycles (psram_word_model's header): a fetch in 35, a line read in 47
    with its readies, a line write's one response in 49 with the word fields passed on,
    and a fetch right after a line read's last word waits until the controller is ready"""
    b = Bench(dut, p_ready=1.0, max_delay=1, controller=True)
    await b.reset()
    for kind, cycles, readies in (("fetch", 36, 1), ("load", 47, 8), ("store", 49, 1)):
        a = Access(kind, True)
        await b.drain()
        got = n = 0
        for _ in range(60):
            n += 1
            ir, dr, _ = await (b.tick(0, a, None) if kind == "fetch" else b.tick(0, None, a))
            got += 1 if (ir if kind == "fetch" else dr) else 0
            if got == readies:
                break
        assert (n, got) == (cycles, readies), f"{kind}: {got} readies in {n} cycles"
    await b.drain()
    a = Access("load", True)
    for _ in range(LINE * 8):
        _, dr, _ = await b.tick(0, None, a)
        if a.finished(dr):
            break
    assert not b.busy
    # The fetch raised at once: refused until the controller's ready, 3 cycles after the word
    f = Access("fetch", True)
    n = 0
    for _ in range(60):
        n += 1
        ir, _, _ = await b.tick(0, f, None)
        if ir:
            break
    assert n == 2 + 36, f"the fetch after a burst read took {n} cycles"
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_soft_reset_requests_nothing(dut):
    """While soft_rst is high a window fetch is neither requested nor completed; once it
    falls the request goes out"""
    b = Bench(dut, p_ready=1.0, max_delay=2)
    await b.reset()
    a = Access("fetch", True)
    for _ in range(5):
        ready, _, accepted = await b.tick(1, a, None)
        assert not ready and not accepted
    await b.access("fetch", True)
    fcov.export()


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_random(dut):
    """Random accesses, windows, gaps, memory delays and refusals against the models"""
    b = Bench(dut, p_ready=0.6, max_delay=6)
    await b.reset()
    for _ in range(600):
        await b.access(random.choice(KINDS), random.random() < 0.6, gap=random.choice((0, 0, 1, 2)))
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_owner_directed(dut):
    """Both sides at once: a data access raised while the fetch's request is in flight
    waits and the response completes only the fetch; both raised in the same cycle (the
    data side goes first); the fetch's select held low while the data side owns the
    bridge and raised in the data's response cycle (fill_hold dropping), for a load and a
    store"""
    b = Bench(dut, p_ready=1.0, min_delay=3, max_delay=3)
    await b.reset()
    for kind in ("load", "store"):
        assert await b.both(Access("fetch", True), Access(kind, True), lead=1) == ["fetch", kind]
        assert await b.both(Access("fetch", True), Access(kind, True), lead=0) == [kind, "fetch"]
        assert await b.both(Access(kind, True), Access("fetch", True), lead=1) == [kind, "fetch"]
        assert await b.both(Access(kind, True), Access("fetch", True), lead=0, hold=True) == [kind, "fetch"]
        assert await b.both(Access(kind, True), Access("fetch", True), lead=2, hold=True) == [kind, "fetch"]
    # A refusing memory: the data side's request is the one held until accepted
    b.mem.p_ready = 0.2
    assert await b.both(Access("fetch", True), Access("load", True), lead=0) == ["load", "fetch"]
    fcov.export()


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_random_both_sides(dut):
    """Both sides holding random accesses independently, with fill_hold, against the models"""
    b = Bench(dut, p_ready=0.6, max_delay=6)
    await b.reset()
    await b.run_sides(3000)
    fcov.export()
