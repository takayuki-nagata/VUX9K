# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""soc_xmem_stub: the memory controller's request/response contract (the module's header,
psram_controller docs/integration.md §3) with soc_pkg's latencies: acceptance only while
idle, one rsp_valid per request after XMEM_READ_LATENCY / XMEM_WRITE_LATENCY cycles,
back-to-back requests in the response cycle, byte strobes, the 2 KB repeating; then random
traffic against a model, checked every cycle."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

READ_LATENCY = 35  # soc_pkg::XMEM_READ_LATENCY
WRITE_LATENCY = 34  # soc_pkg::XMEM_WRITE_LATENCY
WORDS = 512  # soc_pkg::XMEM_WORDS
STROBES = range(16)


class Model:
    """The stub as the contract describes it. Memory bytes nobody wrote are unknown (None):
    the stub has no initial contents."""

    def __init__(self):
        self.mem: dict[int, list] = {}
        self.busy_until = -1  # the response cycle of the request in flight
        self.rdata = None  # its read data (None: a write)

    def ready(self, cycle):
        return cycle >= self.busy_until

    def accept(self, cycle, we, addr, wdata, wstrb):
        idx = (addr >> 2) % WORDS
        word = self.mem.setdefault(idx, [None] * 4)
        if we:
            for b in range(4):
                if wstrb >> b & 1:
                    word[b] = wdata >> (8 * b) & 0xFF
            self.rdata = None
        else:
            self.rdata = list(word)
        self.busy_until = cycle + (WRITE_LATENCY if we else READ_LATENCY)


# What the stub was asked: reads and writes, issued after an idle gap or back to back (in
# the previous response's cycle), and every byte-strobe pattern of a write
@fcov.point("xmem.kind", ("read", "write"), xf=lambda kind, issue, wstrb: kind)
@fcov.point("xmem.issue", ("gap", "back_to_back"), xf=lambda kind, issue, wstrb: issue)
@fcov.cross("xmem.kind_x_issue", ("xmem.kind", "xmem.issue"))
@fcov.point("xmem.wstrb", STROBES, xf=lambda kind, issue, wstrb: wstrb if kind == "write" else None)
def sample(kind, issue, wstrb):
    pass


class Bench:
    """Drives one request at a time like a requester bound by the contract (fields held
    until accepted) and checks the outputs against the model in every cycle."""

    def __init__(self, dut):
        self.dut = dut
        self.model = Model()
        self.cycle = 0
        self.last_rsp = -2  # cycle of the last response

    async def reset(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
        await FallingEdge(dut.clk)
        dut.rst.value = 0
        dut.req_valid.value = 0
        dut.req_we.value = 0
        dut.req_addr.value = 0
        dut.req_wdata.value = 0
        dut.req_wstrb.value = 0
        await FallingEdge(dut.clk)
        dut.rst.value = 1

    async def tick(self, valid, we=0, addr=0, wdata=0, wstrb=0):
        """One cycle with these inputs; returns True if the request was accepted."""
        dut, m, c = self.dut, self.model, self.cycle
        dut.req_valid.value = valid
        dut.req_we.value = we
        dut.req_addr.value = addr
        dut.req_wdata.value = wdata
        dut.req_wstrb.value = wstrb
        await Timer(1, unit="ns")
        ready = int(dut.req_ready.value)
        rsp = int(dut.rsp_valid.value)
        assert ready == m.ready(c), f"cycle {c}: req_ready={ready}, response due in cycle {m.busy_until}"
        assert rsp == (c == m.busy_until), f"cycle {c}: rsp_valid={rsp}, response due in cycle {m.busy_until}"
        if rsp:
            self.last_rsp = c
            if m.rdata is not None and None not in m.rdata:  # the tests read only written words
                want = sum(v << (8 * b) for b, v in enumerate(m.rdata))
                got = dut.rsp_rdata.value
                assert got.is_resolvable and int(got) == want, f"cycle {c}: read {got}, want {want:08x}"
        taken = bool(valid and ready)
        if taken:
            issue = "back_to_back" if self.last_rsp == c else "gap"
            sample("write" if we else "read", issue, wstrb)
            m.accept(c, we, addr, wdata, wstrb)
        await FallingEdge(dut.clk)
        self.cycle += 1
        return taken

    async def request(self, we, addr, wdata=0, wstrb=0xF, gap=None):
        """Hold the request until it is accepted: at once (gap None: taken back to back in
        the response cycle of a request in flight), or `gap` idle cycles after that
        request's response."""
        if gap is not None:
            await self.wait_response()
            for _ in range(gap):
                await self.tick(0)
        for _ in range(READ_LATENCY + 2):
            if await self.tick(1, we, addr, wdata, wstrb):
                return self.cycle - 1
        raise AssertionError("request never accepted")

    async def wait_response(self):
        """Idle until the request in flight has answered; returns rsp_rdata as it stays after
        the response (a read's word; X after a write that merged into an unwritten word)."""
        for _ in range(READ_LATENCY + 2):
            if self.cycle > self.model.busy_until:
                return self.dut.rsp_rdata.value
            await self.tick(0)
        raise AssertionError("no response")


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_latency_and_ready(dut):
    """A write answers XMEM_WRITE_LATENCY cycles after its acceptance, a read
    XMEM_READ_LATENCY; rsp_valid for one cycle; req_ready low in between (the model checks
    every cycle)"""
    b = Bench(dut)
    await b.reset()
    t = await b.request(1, 0x8000_0010, 0xCAFE_F00D, gap=3)  # (nothing in flight yet)
    await b.wait_response()
    assert b.last_rsp - t == WRITE_LATENCY
    t = await b.request(0, 0x8000_0010, gap=0)
    assert int(await b.wait_response()) == 0xCAFE_F00D
    assert b.last_rsp - t == READ_LATENCY
    fcov.export()


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_back_to_back(dut):
    """A request held through the previous one's response is taken in that response's cycle"""
    b = Bench(dut)
    await b.reset()
    prev = await b.request(1, 0x8000_0100, 0x1111_1111)
    for k, (we, addr, data) in enumerate(
        ((1, 0x8000_0104, 0x2222_2222), (0, 0x8000_0100, 0), (0, 0x8000_0104, 0), (1, 0x8000_0100, 0x3333_3333))
    ):
        t = await b.request(we, addr, data)
        assert t == b.last_rsp, f"request {k}: accepted in cycle {t}, previous response in {b.last_rsp}"
        assert t - prev == (WRITE_LATENCY if k in (0, 1) else READ_LATENCY)
        prev = t
    # the reads of the loop returned the written words: checked by the model in tick()
    await b.wait_response()
    fcov.export()


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_byte_strobes_and_aliasing(dut):
    """Every strobe pattern over a known word, read back through aliases of the address
    (upper bits and the byte offset are ignored: the 2 KB repeat)"""
    b = Bench(dut)
    await b.reset()
    for wstrb in STROBES:
        await b.request(1, 0x8000_0200, 0xA5A5_A5A5)
        await b.request(1, 0x8000_0200, 0x0102_0304, wstrb)
        alias = 0x8000_0200 | random.choice((0, 0x800, 0x0FFF_F800)) | random.randrange(4)
        await b.request(0, alias)
        got = int(await b.wait_response())
        want = sum((0x0102_0304 if wstrb >> i & 1 else 0xA5A5_A5A5) & (0xFF << 8 * i) for i in range(4))
        assert got == want, f"wstrb {wstrb:04b}: read {got:08x} at {alias:08x}, want {want:08x}"
    fcov.export()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_random(dut):
    """Random reads and writes (addresses in a few words and their aliases, random strobes,
    back to back or 0-2 idle cycles after the previous response) against the model, every
    cycle"""
    b = Bench(dut)
    await b.reset()
    words = random.sample(range(WORDS), 8)
    for w in words:  # known contents first: unwritten words are X
        await b.request(1, 0x8000_0000 | w << 2, random.getrandbits(32))
    for _ in range(400):
        addr = 0x8000_0000 | random.choice(words) << 2 | random.getrandbits(28) & ~0x7FF | random.randrange(4)
        we, gap = int(random.random() < 0.5), random.choice((None, None, 0, 2))
        await b.request(we, addr, random.getrandbits(32), random.choice(STROBES), gap=gap)
    await b.wait_response()
    fcov.export()
