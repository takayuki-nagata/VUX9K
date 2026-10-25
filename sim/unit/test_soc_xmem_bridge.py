# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_xmem_bridge: an access the caches pass on (fetch_sel / data_sel, their mem_sel)
becomes exactly one request of the memory controller's contract, held until accepted, and
completes (instr_ready / data_ready) in the response's cycle; accesses the caches don't
pass on (out of the window, or hits) complete at once and request nothing; nothing is
requested while the CPU is held in soft reset. Both sides may hold an access at once (the
data cache's write-back while the instruction cache holds its miss): the data side's
request goes first, the other side waits, and a response completes only the side whose
request it answers. A CPU-and-caches model holds each access until it completes, a memory
model behind the bridge accepts and answers with random delays (the contract's lower
bounds, not the PSRAM controller's latencies); every output is checked in every cycle."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

KINDS = ("fetch", "load", "store")
OWNER = ("fetch_done", "data_done", "other_side_waits")


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


class Memory:
    """Behind the bridge: takes a request when ready (randomly low), answers after a
    random delay with one rsp_valid cycle."""

    def __init__(self, p_ready, max_delay, min_delay=1):
        self.p_ready = p_ready
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.due = None  # cycle of the pending response
        self.ready = True
        self.rdata = 0

    def outputs(self, cycle):
        """req_ready, rsp_valid, rsp_rdata for this cycle."""
        if self.due is None:
            self.ready = random.random() < self.p_ready
        rsp = self.due == cycle
        if rsp:
            self.rdata = random.getrandbits(32)
        # As the controller: ready again in the response cycle
        return int(self.ready and (self.due is None or rsp)), int(rsp), self.rdata

    def clock(self, cycle, accepted, rsp):
        if rsp:
            self.due = None
        if accepted:
            self.due = cycle + random.randint(self.min_delay, self.max_delay)


class Access:
    """One access a side holds until it completes. hold (fetch only) is the instruction
    cache's fill_hold: its select is low while the data side owns the bridge."""

    def __init__(self, kind, window):
        self.kind, self.window = kind, window
        self.addr = random.getrandbits(21)
        self.wdata, self.wstrb = random.getrandbits(32), random.randrange(16)
        self.waited = False  # the memory kept this access's request waiting
        self.hold = False
        self.cycles = 0

    @property
    def sel(self):
        return self.window and not self.hold

    def done(self):
        sample(self.kind, "in" if self.window else "out", "after_wait" if self.waited else "at_once")


class Bench:
    def __init__(self, dut, p_ready=0.7, max_delay=5, min_delay=1):
        self.dut = dut
        self.mem = Memory(p_ready, max_delay, min_delay)
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
        dut.rsp_rdata.value = 0
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
        req_ready, rsp_valid, rsp_rdata = self.mem.outputs(c)
        d.req_ready.value, d.rsp_valid.value, d.rsp_rdata.value = req_ready, rsp_valid, rsp_rdata
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
            if store:
                assert (int(d.req_wdata.value), int(d.req_wstrb.value)) == (data.wdata, data.wstrb), (
                    f"{ctx}: write data"
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
        if want_valid and not req_ready:
            (data if dsel else fetch).waited = True
        self.presenting = ("data" if dsel else "fetch") if want_valid and not req_ready else None
        self.mem.clock(c, accepted, rsp_valid)
        if accepted:
            self.busy, self.busy_data = True, dsel
        elif rsp_valid:
            self.busy = False
        await FallingEdge(d.clk)
        self.cycle += 1
        return want_instr, want_data, accepted

    async def access(self, kind, window, gap=0):
        """Idle `gap` cycles, then hold one access until it completes."""
        for _ in range(gap):
            await self.tick()
        a = Access(kind, window)
        for _ in range(100):
            if kind == "fetch":
                ready, _, _ = await self.tick(0, a, None)
            else:
                _, ready, _ = await self.tick(0, None, a)
            if ready:
                a.done()
                return
        raise AssertionError(f"{kind} at word {a.addr:06x} never completed")

    def fill_hold(self):
        """The instruction cache's fill_hold while the data side owns the bridge: dropped in
        the data's response cycle (the write-back's last), so the fetch's select rises in it."""
        return self.busy and self.busy_data and self.mem.due != self.cycle

    async def both(self, first, second, lead, hold=False, max_cycles=100):
        """first raises its select, second (the other side) `lead` cycles later; each held
        until it completes. hold: the fetch's select follows fill_hold(). Returns the
        kinds in their order of completion."""
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
            if f is not None and not f.hold and ir:
                f.done()
                order.append(f.kind)
                sides["fetch"] = None
            if dt is not None and dr:
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
                if ready:
                    a.done()
                    sides[s] = None
                    # A data access right after the last one only while no fetch waits: the
                    # data side's priority would starve it (the CPU never does this)
                    gaps[s] = random.choice((0, 0, 1, 2, 3) if s == "fetch" or sides["fetch"] is None else (1, 2, 3))
                elif a.cycles > 100:
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
