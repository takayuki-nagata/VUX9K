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
model has no initial contents either. The first write of each word has all four strobes.

Bursts (test_burst): every length up to soc_pkg::XMEM_MAX_BURST_WORDS, reads and writes, at
addresses aligned to 4N. Both sides must agree on the words' cycles, rsp_last and wdata_next
and meet the controller's table: a read's word k in 32 + 2k, ready in 33 + 2N; a write's
wdata_next in 28 + 2k, its one response and ready in 32 + 2N. A write's word k is presented
only in cycle 29 + 2k, with its own strobes (0000 included) and X in the disabled bytes;
every other cycle after acceptance has req_valid low and garbage in the request fields."""

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge
from cocotb.types import LogicArray

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
MAX_BURST_WORDS = 8  # soc_pkg::XMEM_MAX_BURST_WORDS
MAX_BURSTS = 150  # step cap of the random burst sequence
BURST_READ_FIRST = 32  # a burst read's word 0
BURST_WRITE_NEXT = 28  # + 2k: a burst write's wdata_next of word k


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


# Bursts: every length of both kinds, and every strobe pattern on a word after the first
@fcov.point("psram.burst_read", range(2, MAX_BURST_WORDS + 1))
def sample_burst_read(words):
    pass


@fcov.point("psram.burst_write", range(2, MAX_BURST_WORDS + 1))
def sample_burst_write(words):
    pass


@fcov.point("psram.burst_wstrb", STROBES)
def sample_burst_wstrb(wstrb):
    pass


def with_x(data, wstrb):
    """req_wdata with X in the bytes wstrb disables: the contract allows it"""
    return LogicArray("".join(f"{data >> (8 * b) & 0xFF:08b}" if wstrb >> b & 1 else "X" * 8 for b in range(3, -1, -1)))


class Bench:
    """Inputs are driven, and outputs sampled, at falling clock edges: what is read there
    is the state after the previous rising edge, and what is driven there is taken at the
    next one. cycle counts the rising edges since reset release."""

    def __init__(self, dut):
        self.dut = dut
        self.cycle = 0
        self.mem: dict[int, list[int | None]] = {}  # word index -> 4 bytes (only written words)
        self.responses: dict[int, tuple[int | None, bool]] = {}  # cycle -> (read word or None, rsp_last)
        self.ready_at: int | None = None  # when the request in flight ends
        self.word_cycles: dict[int, tuple[int, int]] = {}  # cycle -> a burst write's (word, wstrb)
        self.wdata_next: set[int] = set()
        self.last_ready = -2
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

    def drive(self, valid, we=0, addr=0, wdata=0, wstrb=0, length=0):
        dut = self.dut
        dut.req_valid.value = valid
        dut.req_we.value = we
        dut.req_addr.value = addr
        dut.req_wdata.value = wdata
        dut.req_wstrb.value = wstrb
        dut.req_len.value = length

    def drive_between(self):
        """A cycle inside a request: its word if one is due, else garbage (all don't-care)"""
        word = self.word_cycles.pop(self.cycle, None)
        if word is not None:
            data, wstrb = word
            self.drive(0, random.getrandbits(1), random.getrandbits(24), with_x(data, wstrb), wstrb)
        else:
            self.drive(0, random.getrandbits(1), random.getrandbits(24), random.getrandbits(32), random.getrandbits(4))
        self.dut.req_len.value = random.randrange(MAX_BURST_WORDS)

    def check_cycle(self):
        """Both sides' outputs in this cycle; returns req_ready."""
        dut, c = self.dut, self.cycle
        for name in ("req_ready", "rsp_valid", "rsp_last", "wdata_next", "init_done"):
            ctl, mdl = getattr(dut, f"ctl_{name}").value, getattr(dut, f"mdl_{name}").value
            assert ctl.is_resolvable and mdl.is_resolvable, f"cycle {c}: {name} ctl={ctl} mdl={mdl}"
            assert int(ctl) == int(mdl), f"cycle {c}: {name} controller={ctl}, word model={mdl}"
        ready = int(dut.ctl_req_ready.value)
        assert int(dut.ctl_init_done.value) == (c >= INIT_CYCLES), f"cycle {c}: init_done"
        if ready and self.first_ready is None:
            self.first_ready = c
        rsp = int(dut.ctl_rsp_valid.value)
        assert rsp == (c in self.responses), f"cycle {c}: rsp_valid={rsp}, responses due {sorted(self.responses)}"
        if rsp:
            want, last = self.responses.pop(c)
            assert int(dut.ctl_rsp_last.value) == last, f"cycle {c}: rsp_last, want {last}"
            if want is not None:
                ctl, mdl = dut.ctl_rsp_rdata.value, dut.mdl_rsp_rdata.value
                assert ctl.is_resolvable and int(ctl) == want, f"cycle {c}: controller read {ctl}, want {want:08x}"
                assert mdl.is_resolvable and int(mdl) == want, f"cycle {c}: word model read {mdl}, want {want:08x}"
        else:
            assert not int(dut.ctl_rsp_last.value), f"cycle {c}: rsp_last without rsp_valid"
        nxt = int(dut.ctl_wdata_next.value)
        assert nxt == (c in self.wdata_next), f"cycle {c}: wdata_next={nxt}, due {sorted(self.wdata_next)}"
        self.wdata_next.discard(c)
        if c == self.ready_at:
            self.ready_at = None
            self.last_ready = c
        assert ready == (c >= INIT_CYCLES and self.ready_at is None), f"cycle {c}: req_ready={ready}"
        return ready

    async def tick(self, valid, we=0, addr=0, wdata=0, wstrb=0, issue=None):
        """One cycle with these inputs; returns True if the request was accepted."""
        ready = self.check_cycle()
        self.drive(valid, we, addr, wdata, wstrb)
        taken = bool(valid and ready)
        if taken:
            self.accept(we, addr, wdata, wstrb, issue or ("back_to_back" if self.last_ready == self.cycle else "gap"))
        await FallingEdge(self.dut.clk)
        self.cycle += 1
        return taken

    def sample_addr(self, idx):
        for bit in range(ADDR_BITS):
            if idx >> bit & 1:
                sample_addr_bit(bit)

    def store(self, idx, wdata, wstrb):
        word = self.mem.setdefault(idx, [None] * 4)
        for b in range(4):
            if wstrb >> b & 1:
                word[b] = wdata >> (8 * b) & 0xFF
        assert None not in word, "the first write of a word must set all bytes"

    def load(self, idx):
        return sum((v or 0) << (8 * b) for b, v in enumerate(self.mem[idx]))

    def accept(self, we, addr, wdata, wstrb, issue):
        idx = addr >> 2 & ((1 << ADDR_BITS) - 1)
        sample("write" if we else "read", issue, wstrb)
        self.sample_addr(idx)
        if we:
            self.store(idx, wdata, wstrb)
            self.ready_at = self.cycle + WRITE_LATENCY
            self.responses[self.ready_at] = (None, True)
        else:
            self.ready_at = self.cycle + READ_LATENCY
            self.responses[self.ready_at] = (self.load(idx), True)

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

    async def burst_read(self, idx, words, hold=True):
        """Raise a burst read, held until accepted (hold: back to back, else after a gap of 1)"""
        await self.finish_writes()
        if not hold:
            await self.wait_response()
            await self.between()
        length = words - 1
        wdata, wstrb = random.getrandbits(32), random.getrandbits(4)  # held like the rest of the request
        for _ in range(READ_LATENCY + 2 * MAX_BURST_WORDS):
            ready = self.check_cycle()
            self.drive(1, 0, idx << 2, wdata, wstrb, length)
            taken = bool(ready)
            if taken:
                self.sample_addr(idx)
                sample_burst_read(words)
                for k in range(words):
                    self.responses[self.cycle + BURST_READ_FIRST + 2 * k] = (self.load(idx + k), k == length)
                self.ready_at = self.cycle + BURST_READ_FIRST + 1 + 2 * words
            await FallingEdge(self.dut.clk)
            self.cycle += 1
            if taken:
                return
        raise AssertionError("burst read not accepted")

    async def burst_write(self, idx, data, strobes):
        """A burst write: word 0 at acceptance, each later word only in its own cycle"""
        await self.finish_writes()
        words = len(data)
        length = words - 1
        for _ in range(READ_LATENCY + 2 * MAX_BURST_WORDS):
            ready = self.check_cycle()
            self.drive(1, 1, idx << 2, with_x(data[0], strobes[0]), strobes[0], length)
            taken = bool(ready)
            if taken:
                self.sample_addr(idx)
                sample_burst_write(words)
                for k in range(words):
                    self.store(idx + k, data[k], strobes[k])
                    if k:
                        sample_burst_wstrb(strobes[k])
                        self.wdata_next.add(self.cycle + BURST_WRITE_NEXT + 2 * k)
                        self.word_cycles[self.cycle + BURST_WRITE_NEXT + 1 + 2 * k] = (data[k], strobes[k])
                self.ready_at = self.cycle + BURST_WRITE_NEXT + 4 + 2 * words
                self.responses[self.ready_at] = (None, True)
            await FallingEdge(self.dut.clk)
            self.cycle += 1
            if taken:
                return
        raise AssertionError("burst write not accepted")

    async def between(self):
        """One cycle of a request in flight (or idle): its due word or garbage, req_valid low"""
        self.check_cycle()
        self.drive_between()
        await FallingEdge(self.dut.clk)
        self.cycle += 1

    async def finish_writes(self):
        """A burst write's words come first: a raised request must hold its fields"""
        for _ in range(2 * MAX_BURST_WORDS + BURST_WRITE_NEXT + 2):
            if not self.word_cycles:
                return
            await self.between()
        raise AssertionError("burst write words not sent")

    async def wait_response(self):
        for _ in range(READ_LATENCY + 2 * MAX_BURST_WORDS + 2):
            if self.ready_at is None:
                return
            await self.between()
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


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_burst(dut):
    """Every burst length, reads and writes with per-word strobes, both sides checked every
    cycle and read back; the highest address too"""
    b = Bench(dut)
    await b.reset()
    for _ in range(INIT_CYCLES + 1):
        await b.tick(0)
    # Whole lines first: every later word has all its bytes
    top = (1 << ADDR_BITS) - MAX_BURST_WORDS
    lines = [0, MAX_BURST_WORDS, top] + [(1 << i) for i in range(3, ADDR_BITS)]
    lines += [w & -MAX_BURST_WORDS for w in random.sample(range(1 << ADDR_BITS), 8)]
    lines = list(dict.fromkeys(lines))
    for line in lines:
        await b.burst_write(line, [random.getrandbits(32) for _ in range(MAX_BURST_WORDS)], [0xF] * MAX_BURST_WORDS)
        await b.burst_read(line, MAX_BURST_WORDS)
    # Every length directed; every strobe on a later word
    for words in range(2, MAX_BURST_WORDS + 1):
        base = lines[0] + (0 if words > MAX_BURST_WORDS // 2 else words)
        base -= base % words
        await b.burst_write(base, [random.getrandbits(32) for _ in range(words)], [0xF] + [0] * (words - 1))
        await b.burst_read(base, words)
    # A single word raised in the cycles after a burst read's last word: held until ready
    await b.burst_read(lines[0], MAX_BURST_WORDS)
    await b.request(0, lines[0] << 2, limit=BURST_READ_FIRST + 2 + 2 * MAX_BURST_WORDS)
    await b.wait_response()
    for wstrb in STROBES:
        await b.burst_write(lines[1], [random.getrandbits(32) for _ in range(2)], [random.choice(STROBES), wstrb])
        await b.burst_read(lines[1], 2, hold=False)
    for _ in range(MAX_BURSTS):
        words = random.randint(2, MAX_BURST_WORDS)
        # Aligned to 4N, inside a line (line 0 has a place for every length)
        base = random.choice(
            [w for line in lines for w in range(line, line + MAX_BURST_WORDS - words + 1) if w % words == 0]
        )
        if random.random() < 0.5:
            data = [random.getrandbits(32) for _ in range(words)]
            await b.burst_write(base, data, [random.choice(STROBES) for _ in range(words)])
        else:
            await b.burst_read(base, words, hold=random.random() < 0.5)
    await b.wait_response()
    for line in lines:
        await b.burst_read(line, MAX_BURST_WORDS)
    await b.wait_response()
    b.check_w955()
    fcov.export()
