# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""psram_word_model against the real controller (tb_psram_contract, Icarus only): the
controller on the pin-level W955D8MBYA model and the word model get the same clock, reset
and requests, and must answer alike in every cycle: req_ready and init_done rise in the
same cycle, soc_pkg::PSRAM_INIT_CYCLES after reset release; a request raised during the
power-up is held and taken by both in that cycle; then a seeded random sequence (reads of
written words, every byte strobe, back to back and after gaps, word addresses with every
one of the 21 bits set) gets rsp_valid in the same cycle, XMEM_READ_LATENCY (35) /
XMEM_WRITE_LATENCY (34) after acceptance, with equal read data, which is also checked
against a Python memory. The W955 model's protocol/timing checkers must count no error.

Only written words are read: the W955 model's memory starts unknown (X), and the word
model has no initial contents either. The first write of each word has all four strobes."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

READ_LATENCY = 35  # soc_pkg::XMEM_READ_LATENCY
WRITE_LATENCY = 34  # soc_pkg::XMEM_WRITE_LATENCY
# soc_pkg::PSRAM_INIT_CYCLES as psram_core derives it at 18 MHz: tRP 400 ns and tVCS
# 200 us in cycles, rounded up, plus one cycle in each of the two states. The test also
# reads the tb's init_cycles output (the constant itself) and checks both.
CLK_HZ = 18_000_000  # soc_pkg::CLK_HZ
INIT_CYCLES = -(-400 * CLK_HZ // 10**9) + -(-200_000 * CLK_HZ // 10**9) + 2
# soc_top holds the CPU in reset until por_counter[15] is set: 2^15 cycles after its
# reset synchronizer, so the PSRAM is ready long before the first access.
POR_CYCLES = 1 << 15
ADDR_BITS = 21  # word address bits: req_addr[22:2]
CLK_PERIOD_PS = 2 * round(1e12 / CLK_HZ / 2)
STROBES = range(16)
MAX_REQUESTS = 300  # step cap of the random sequence


# What both were asked: reads and writes; issued after an idle gap, back to back (in the
# previous response's cycle) or raised during the power-up; every strobe pattern of a
# write; and every word-address bit set in some request
@fcov.point("psram.kind", ("read", "write"), xf=lambda kind, issue, wstrb: kind)
@fcov.point("psram.issue", ("gap", "back_to_back", "during_init"), xf=lambda kind, issue, wstrb: issue)
@fcov.point("psram.wstrb", STROBES, xf=lambda kind, issue, wstrb: wstrb if kind == "write" else None)
def sample(kind, issue, wstrb):
    pass


@fcov.point("psram.addr_bit", range(ADDR_BITS))
def sample_addr_bit(bit):
    pass


class Bench:
    """Inputs are driven, and outputs sampled, at falling clock edges: what is read there
    is the state after the previous rising edge, and what is driven there is taken at the
    next one. cycle counts the rising edges since reset release."""

    def __init__(self, dut):
        self.dut = dut
        self.cycle = 0
        self.mem: dict[int, list[int | None]] = {}  # word index -> 4 bytes (only written words)
        self.pending: tuple[int, int | None] | None = None  # (response cycle, read word or None)
        self.last_rsp = -2
        self.first_ready: int | None = None

    async def reset(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())
        dut.rst_n.value = 0
        self.drive(0)
        for _ in range(5):
            await FallingEdge(dut.clk)
        dut.rst_n.value = 1  # released between this falling edge and rising edge 1
        self.cycle = 0

    def drive(self, valid, we=0, addr=0, wdata=0, wstrb=0):
        dut = self.dut
        dut.req_valid.value = valid
        dut.req_we.value = we
        dut.req_addr.value = addr
        dut.req_wdata.value = wdata
        dut.req_wstrb.value = wstrb

    def check_cycle(self):
        """Both sides' outputs in this cycle; returns req_ready."""
        dut, c = self.dut, self.cycle
        for name in ("req_ready", "rsp_valid", "init_done"):
            ctl, mdl = getattr(dut, f"ctl_{name}").value, getattr(dut, f"mdl_{name}").value
            assert ctl.is_resolvable and mdl.is_resolvable, f"cycle {c}: {name} ctl={ctl} mdl={mdl}"
            assert int(ctl) == int(mdl), f"cycle {c}: {name} controller={ctl}, word model={mdl}"
        ready = int(dut.ctl_req_ready.value)
        assert int(dut.ctl_init_done.value) == (c >= INIT_CYCLES), f"cycle {c}: init_done"
        if ready and self.first_ready is None:
            self.first_ready = c
        rsp = int(dut.ctl_rsp_valid.value)
        due, want = self.pending if self.pending else (None, None)
        assert rsp == (c == due), f"cycle {c}: rsp_valid={rsp}, response due in cycle {due}"
        if rsp:
            if want is not None:
                ctl, mdl = dut.ctl_rsp_rdata.value, dut.mdl_rsp_rdata.value
                assert ctl.is_resolvable and int(ctl) == want, f"cycle {c}: controller read {ctl}, want {want:08x}"
                assert mdl.is_resolvable and int(mdl) == want, f"cycle {c}: word model read {mdl}, want {want:08x}"
            self.pending = None
            self.last_rsp = c
        assert ready == (c >= INIT_CYCLES and self.pending is None), f"cycle {c}: req_ready={ready}"
        return ready

    async def tick(self, valid, we=0, addr=0, wdata=0, wstrb=0, issue=None):
        """One cycle with these inputs; returns True if the request was accepted."""
        ready = self.check_cycle()
        self.drive(valid, we, addr, wdata, wstrb)
        taken = bool(valid and ready)
        if taken:
            self.accept(we, addr, wdata, wstrb, issue or ("back_to_back" if self.last_rsp == self.cycle else "gap"))
        await FallingEdge(self.dut.clk)
        self.cycle += 1
        return taken

    def accept(self, we, addr, wdata, wstrb, issue):
        idx = addr >> 2 & ((1 << ADDR_BITS) - 1)
        sample("write" if we else "read", issue, wstrb)
        for bit in range(ADDR_BITS):
            if idx >> bit & 1:
                sample_addr_bit(bit)
        if we:
            word = self.mem.setdefault(idx, [None] * 4)
            for b in range(4):
                if wstrb >> b & 1:
                    word[b] = wdata >> (8 * b) & 0xFF
            assert None not in word, "the first write of a word must set all bytes"
            self.pending = (self.cycle + WRITE_LATENCY, None)
        else:
            self.pending = (self.cycle + READ_LATENCY, sum((v or 0) << (8 * b) for b, v in enumerate(self.mem[idx])))

    async def request(self, we, addr, wdata=0, wstrb=0xF, gap=None, limit=READ_LATENCY + 2, issue=None):
        """Hold the request until accepted: at once (gap None: back to back in the response
        cycle of a request in flight) or `gap` idle cycles after that response."""
        if gap is not None:
            await self.wait_response()
            for _ in range(gap):
                await self.tick(0)
        for _ in range(limit):
            if await self.tick(1, we, addr, wdata, wstrb, issue):
                return self.cycle - 1
        raise AssertionError(f"request not accepted within {limit} cycles")

    async def wait_response(self):
        for _ in range(READ_LATENCY + 2):
            if self.pending is None:
                return
            await self.tick(0)
        raise AssertionError("no response")

    def check_w955(self):
        errors = int(self.dut.u_w955.error_count.value)
        assert errors == 0, f"W955D8MBYA model counted {errors} protocol/timing errors (see the log)"


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def test_init_and_held_request(dut):
    """req_ready/init_done rise together on both sides PSRAM_INIT_CYCLES after reset
    release; a write raised during the power-up is taken by both in that cycle"""
    assert POR_CYCLES > INIT_CYCLES, "soc_top's power-on reset must outlast the PSRAM power-up"
    b = Bench(dut)
    await b.reset()
    # Read after the reset: at time 0 the continuous assignment hasn't been evaluated (X)
    assert int(dut.init_cycles.value) == INIT_CYCLES == 3610, f"PSRAM_INIT_CYCLES = {int(dut.init_cycles.value)}"
    for _ in range(100):
        await b.tick(0)
    addr = 0x7F_FFFC  # top word: every address bit set
    t = await b.request(1, addr, 0x1234_5678, limit=INIT_CYCLES + 2, issue="during_init")
    assert t == b.first_ready == INIT_CYCLES, f"accepted in cycle {t}, ready from {b.first_ready}"
    await b.request(0, addr | 3)  # back to back; addr[1:0] ignored
    await b.wait_response()
    b.check_w955()
    fcov.export()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_random(dut):
    """Seeded random reads and writes, both sides checked every cycle"""
    b = Bench(dut)
    await b.reset()
    for _ in range(INIT_CYCLES + 1):
        await b.tick(0)
    # Word indices: 0, all ones, each single bit, and random ones
    words = [0, (1 << ADDR_BITS) - 1] + [1 << i for i in range(ADDR_BITS)]
    words += random.sample(range(1 << ADDR_BITS), 8)
    words = list(dict.fromkeys(words))
    # Every strobe pattern once (directed), each read back
    written = [words[0]]
    await b.request(1, 0, random.getrandbits(32))
    for wstrb in STROBES:
        await b.request(1, 0, random.getrandbits(32), wstrb)
        await b.request(0, 0)
    for _ in range(MAX_REQUESTS):
        gap = random.choice((None, None, 0, 1, 3))
        addr_lo = random.randrange(4)  # ignored by both
        if written and random.random() < 0.5:
            w = random.choice(written)
            await b.request(0, w << 2 | addr_lo, gap=gap)
        else:
            w = random.choice(words)
            wstrb = random.choice(STROBES) if w in written else 0xF
            if w not in written:
                written.append(w)
            await b.request(1, w << 2 | addr_lo, random.getrandbits(32), wstrb, gap=gap)
    await b.wait_response()
    # Every written word read back once more, back to back
    for w in written:
        await b.request(0, w << 2)
    await b.wait_response()
    b.check_w955()
    fcov.export()
