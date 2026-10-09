# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_xmem_bridge: a CPU access in the window becomes exactly one request of the memory
controller's contract, held until accepted, and completes (instr_ready / data_ready) in
the response's cycle; accesses outside the window complete at once and request nothing;
nothing is requested while the CPU is held in soft reset. A CPU model holds each access
until it completes, a memory model behind the bridge accepts and answers with random
delays (the contract's lower bounds, not the stub's latencies); every output is checked
in every cycle."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

KINDS = ("fetch", "load", "store")


# Each access kind inside and outside the window, and whether the memory kept the request
# waiting (req_ready low) before taking it
@fcov.point("xbridge.kind", KINDS, xf=lambda kind, window, waited: kind)
@fcov.point("xbridge.window", ("in", "out"), xf=lambda kind, window, waited: window)
@fcov.cross("xbridge.kind_x_window", ("xbridge.kind", "xbridge.window"))
@fcov.point("xbridge.accept", ("at_once", "after_wait"), xf=lambda kind, window, waited: waited)
def sample(kind, window, waited):
    pass


class Memory:
    """Behind the bridge: takes a request when ready (randomly low), answers after a
    random delay with one rsp_valid cycle."""

    def __init__(self, p_ready, max_delay):
        self.p_ready = p_ready
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
            self.due = cycle + random.randint(1, self.max_delay)


class Bench:
    def __init__(self, dut, p_ready=0.7, max_delay=5):
        self.dut = dut
        self.mem = Memory(p_ready, max_delay)
        self.busy = False  # the bridge's in-flight request, as the contract implies
        self.cycle = 0

    async def reset(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
        await FallingEdge(dut.clk)
        dut.rst.value = 0
        self.drive(soft_rst=0)
        dut.req_ready.value = 1
        dut.rsp_valid.value = 0
        dut.rsp_rdata.value = 0
        await FallingEdge(dut.clk)
        dut.rst.value = 1

    def drive(self, soft_rst=0, kind=None, window=False, addr=0, wdata=0, wstrb=0):
        """The CPU's side for this cycle: kind None is a cycle without FETCH or MEM_WAIT."""
        d = self.dut
        d.soft_rst.value = soft_rst
        d.fetch_req.value = int(kind == "fetch")
        d.fetch_sel.value = int(kind == "fetch" and window)
        d.fetch_addr.value = addr if kind == "fetch" else random.getrandbits(32)
        d.data_req.value = int(kind in ("load", "store"))
        d.data_sel.value = int(kind in ("load", "store") and window)
        d.data_addr.value = addr if kind in ("load", "store") else random.getrandbits(32)
        d.data_we.value = int(kind == "store")
        d.data_wdata.value = wdata
        d.data_wstrb.value = wstrb

    async def tick(self, soft_rst=0, kind=None, window=False, addr=0, wdata=0, wstrb=0):
        """One cycle; returns (ready for this access, whether the memory took a request,
        whether it refused one: req_valid with req_ready low)."""
        d, c = self.dut, self.cycle
        self.drive(soft_rst, kind, window, addr, wdata, wstrb)
        req_ready, rsp_valid, rsp_rdata = self.mem.outputs(c)
        d.req_ready.value, d.rsp_valid.value, d.rsp_rdata.value = req_ready, rsp_valid, rsp_rdata
        await Timer(1, unit="ns")

        in_win = window and kind is not None
        want_valid = in_win and not self.busy and not soft_rst
        done = self.busy and rsp_valid
        ctx = f"cycle {c}: {kind} {'in' if window else 'out of'} window, busy={self.busy}, soft_rst={soft_rst}"
        assert int(d.req_valid.value) == want_valid, f"{ctx}: req_valid={int(d.req_valid.value)}"
        if want_valid:
            assert int(d.req_we.value) == (kind == "store"), f"{ctx}: req_we"
            assert int(d.req_addr.value) == addr, f"{ctx}: req_addr {int(d.req_addr.value):08x}"
            if kind == "store":
                assert (int(d.req_wdata.value), int(d.req_wstrb.value)) == (wdata, wstrb), f"{ctx}: write data"
        want_instr = not (kind == "fetch" and window) or done
        want_data = not (kind in ("load", "store") and window) or done
        assert int(d.instr_ready.value) == want_instr, f"{ctx}: instr_ready={int(d.instr_ready.value)}"
        assert int(d.data_ready.value) == want_data, f"{ctx}: data_ready={int(d.data_ready.value)}"
        assert int(d.rdata.value) == rsp_rdata, f"{ctx}: rdata"

        accepted = want_valid and req_ready
        self.mem.clock(c, accepted, rsp_valid)
        if accepted:
            self.busy = True
        elif rsp_valid:
            self.busy = False
        await FallingEdge(d.clk)
        self.cycle += 1
        return (want_instr if kind == "fetch" else want_data), accepted, want_valid and not req_ready

    async def access(self, kind, window, gap=0):
        """Idle `gap` cycles, then hold one access until it completes."""
        for _ in range(gap):
            await self.tick()
        region = 0x8 if window else random.choice((0x0, 0x2, 0x4, 0x9))
        addr = region << 28 | random.getrandbits(28) & ~3
        wdata, wstrb = random.getrandbits(32), random.randrange(16)
        waited = False
        for _ in range(100):
            ready, _, refused = await self.tick(0, kind, window, addr, wdata, wstrb)
            waited = waited or refused  # the memory kept the request waiting
            if ready:
                sample(kind, "in" if window else "out", "after_wait" if waited else "at_once")
                return
        raise AssertionError(f"{kind} at {addr:08x} never completed")


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
    for _ in range(5):
        ready, accepted, _ = await b.tick(1, "fetch", True, 0x8000_0040)
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
