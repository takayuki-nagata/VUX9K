# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu's wait states: instr_ready = 0 holds a FETCH, data_ready = 0 holds a
MEM_WAIT (RV32 load/store, Hack M read), and nothing else may happen meanwhile.

The harness drives the CPU cycle by cycle from a memory model with soc_ram's timing
(each port returns the word at the address the CPU presented in the previous cycle) and
puts garbage on instr_in/data_in in every cycle whose ready is low, so a CPU that
latched data before ready would compute wrong results. Most checks compare a run with
stalls to the same program without: a stall of n cycles must delay everything after it
by exactly n cycles and change nothing else, and in the stalled cycles the CPU must keep
presenting the request (pc_out, the store's address/data/byte enables with mem_write,
the load's address) with no mem_read_req. Register writes and minstret are observed
through the stores the programs make. Only the CPU's ports are used, so this also runs
on the gate-level netlist.
"""

import random
from typing import NamedTuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from rv32_asm import Asm

MAX_CYCLES = 160
RV_INSTR_GARBAGE = 0xFFFFFFFF  # illegal in RV32; the mode detector would take it for Hack
HACK_INSTR_GARBAGE = 0x00000013  # an RV32 nop: the mode detector would take it for RV32
DATA_GARBAGE = 0xDEADBEEF
IRQ_LINES = ("timer_irq_in", "ext_irq_in", "sw_irq_in")


class Cycle(NamedTuple):
    """The CPU's outputs in one cycle (None: X/Z), and the ready inputs it was given"""

    pc: int | None
    mem_write: int
    waddr: int | None
    wdata: int | None
    wbe: int | None
    read_req: int
    daddr: int | None
    instr_ready: int
    data_ready: int

    def store(self):
        return (self.waddr, self.wdata, self.wbe) if self.mem_write else None

    def key(self):
        """What the CPU did this cycle (store fields only while it writes)"""
        return (self.pc, self.mem_write, self.store(), self.read_req, self.daddr if self.read_req else None)


class Run(NamedTuple):
    trace: list[Cycle]
    dmem: dict[int, int]
    writes: list[tuple[int, int, int, int]]  # (cycle, address, data, byte enables) of completed writes


def val(sig):
    v = sig.value
    return int(v) if v.is_resolvable else None


def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())


def always(_cycle):
    return True


async def run(
    dut,
    imem,
    dmem=None,
    *,
    instr_ready=always,
    data_ready=always,
    irq=None,
    hack=False,
    max_cycles=MAX_CYCLES,
):
    """Reset the CPU and run max_cycles cycles of `imem` ({word address: word}); the
    clock runs already (start_clock, once per test: a test makes several runs).

    instr_ready/data_ready(cycle) give the ready inputs per cycle (cycle 0: the first
    after reset); irq(cycle) the set of IRQ_LINES that are high. dmem ({address: word};
    word-aligned for RV32, Hack's A for Hack) is the data memory, updated by every
    completed write (mem_write && data_ready).
    """
    dmem = dict(dmem or {})
    instr_garbage = HACK_INSTR_GARBAGE if hack else RV_INSTR_GARBAGE
    for sig in ("soft_rst", "data_in", "mtime_in", "boot_mode_valid", "boot_mode", *IRQ_LINES):
        getattr(dut, sig).value = 0
    dut.instr_ready.value = 1
    dut.data_ready.value = 1
    dut.rst.value = 0
    dut.instr_in.value = imem[0]
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    def word_addr(addr):
        return addr if hack else addr & ~3

    trace: list[Cycle] = []
    writes = []
    prev_pc: int | None = 0  # in reset pc_out is the reset vector
    prev_daddr: int | None = None
    for cycle in range(max_cycles):
        if cycle:
            await FallingEdge(dut.clk)
        ir, dr = int(instr_ready(cycle)), int(data_ready(cycle))
        dut.instr_ready.value = ir
        dut.data_ready.value = dr
        dut.instr_in.value = imem.get(prev_pc & ~3, 0x00000013) if ir and prev_pc is not None else instr_garbage
        if dr and prev_daddr is not None:
            dut.data_in.value = dmem.get(word_addr(prev_daddr), 0)
        else:
            dut.data_in.value = DATA_GARBAGE
        lines = irq(cycle) if irq else ()
        for line in IRQ_LINES:
            getattr(dut, line).value = int(line in lines)
        await Timer(1, unit="ns")
        c = Cycle(
            pc=val(dut.pc_out),
            mem_write=int(dut.mem_write.value),
            waddr=val(dut.data_waddr),
            wdata=val(dut.data_out),
            wbe=val(dut.mem_byte_we),
            read_req=int(dut.mem_read_req.value),
            daddr=val(dut.data_addr),
            instr_ready=ir,
            data_ready=dr,
        )
        trace.append(c)
        if c.mem_write and dr:
            assert c.waddr is not None and c.wdata is not None and c.wbe is not None, f"cycle {cycle}: X in a store"
            addr = word_addr(c.waddr)
            old = dmem.get(addr, 0)
            mask = sum(0xFF << (8 * b) for b in range(4) if c.wbe >> b & 1)
            dmem[addr] = (old & ~mask) | (c.wdata & mask)
            writes.append((cycle, c.waddr, c.wdata, c.wbe))
        prev_pc, prev_daddr = c.pc, c.daddr
    return Run(trace, dmem, writes)


def low_during(start, n):
    """A ready schedule: low in cycles start .. start + n - 1"""
    return lambda cycle: not start <= cycle < start + n


def fetch_cycle(trace, pc):
    """The FETCH of `pc` in a run without stalls: pc_out shows the next PC from the cycle
    before (EXECUTE, MEM_WAIT), so FETCH is the last cycle of the first run of `pc`"""
    i = next(i for i, c in enumerate(trace) if c.pc == pc)
    while trace[i + 1].pc == pc:
        i += 1
    return i


def check_stalled(base, stalled, start, n, what):
    """`stalled` = `base` with the request in cycle `start` held for n extra cycles"""
    b, s = base.trace, stalled.trace
    assert [c.key() for c in s[:start]] == [c.key() for c in b[:start]], f"{what}: differs before the stall"
    # The request that completes in b[start]: the fetch address, the store, a load's
    # address (data_addr means nothing outside a load: registers aren't reset between runs)
    done = b[start]

    def request(c):
        return (c.pc, c.mem_write, c.store(), c.daddr if done.read_req else None)

    for i in range(start, start + n):
        assert s[i].read_req == 0, f"{what}: cycle {i} (stalled): mem_read_req before data_ready"
        assert request(s[i]) == request(done), (
            f"{what}: cycle {i} (stalled) shows {s[i]}, expected the request of {done}"
        )
    tail = len(s) - start - n
    for i in range(tail):
        assert s[start + n + i].key() == b[start + i].key(), (
            f"{what}: cycle {start + n + i} is {s[start + n + i]}, expected cycle {start + i} "
            f"of the run without stall, {b[start + i]}"
        )
    assert [w[1:] for w in stalled.writes] == [w[1:] for w in base.writes if w[0] < len(b) - n], (
        f"{what}: completed writes differ"
    )


def program_imem(a):
    return {4 * i: w for i, w in enumerate(a.assemble())}


def fetch_program():
    """Each addi depends on the one before, so an instruction executed twice (a stalled
    FETCH that advances with the old ir) or a garbage word latched shows in the store"""
    a = Asm()
    a.li("t0", 0x5A)
    a.addi("t0", "t0", 1)
    a.label("target")
    a.addi("t0", "t0", 0x10)
    a.addi("t0", "t0", 0x100)
    a.sw("t0", 0x200, "zero")
    a.label("done")
    a.j("done")
    return program_imem(a), a.labels


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_harness_baseline(dut):
    """Positive control: without stalls the program stores its result once, in MEM_WAIT"""
    start_clock(dut)
    imem, _ = fetch_program()
    r = await run(dut, imem)
    assert r.dmem.get(0x200) == 0x16B, f"result {r.dmem.get(0x200)}"
    assert [w[1:] for w in r.writes] == [(0x200, 0x16B, 0xF)], r.writes


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_stalled_fetch_holds_state(dut):
    """instr_ready low in a FETCH (the first after reset, and one mid-program) for 1-4
    cycles: pc_out stays, nothing is written, and the program continues unchanged once
    ready rises. The first FETCH also shows the mode detector garbage that looks like Hack."""
    start_clock(dut)
    imem, labels = fetch_program()
    base = await run(dut, imem)
    for pc in (0, labels["target"]):
        start = fetch_cycle(base.trace, pc)
        for n in (1, 2, 4):
            stalled = await run(dut, imem, instr_ready=low_during(start, n))
            check_stalled(base, stalled, start, n, f"FETCH of 0x{pc:x} stalled {n}")
            assert stalled.dmem.get(0x200) == 0x16B


def irq_program():
    """Timer interrupt enabled; the handler stores mepc and mcause and spins. Without an
    interrupt the main path stores 1 after `target`."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("t0", 0x80)
    a.csrs("mie", "t0")
    a.li("t0", 0x8)
    a.csrs("mstatus", "t0")
    a.li("s0", 0)
    a.nop()
    a.label("target")
    a.addi("s0", "s0", 1)
    a.sw("s0", 0x200, "zero")
    a.label("done")
    a.j("done")
    a.label("handler")
    a.csrr("t1", "mepc")
    a.sw("t1", 0x204, "zero")
    a.csrr("t1", "mcause")
    a.sw("t1", 0x208, "zero")
    a.csrw("mie", "zero")
    a.label("hspin")
    a.j("hspin")
    return program_imem(a), a.labels


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_stalled_fetch_samples_irq_in_last_cycle(dut):
    """The interrupt is sampled in the FETCH cycle that completes: pending only then (or
    from a stalled cycle on) it is taken at `target`; pending only while the FETCH waits,
    it is not"""
    start_clock(dut)
    imem, labels = irq_program()
    target = labels["target"]
    base = await run(dut, imem)
    assert base.dmem.get(0x200) == 1 and 0x204 not in base.dmem, "positive control: no interrupt"
    start, n = fetch_cycle(base.trace, target), 3
    stall = low_during(start, n)
    last = start + n

    cases = (
        ("pending in the completing cycle only", lambda c: c == last, True),
        ("pending from a stalled cycle on", lambda c: c >= start + 1, True),
        ("pending while the FETCH waits, dropped before it completes", lambda c: start <= c < last, False),
    )
    for what, pending, taken in cases:
        r = await run(dut, imem, instr_ready=stall, irq=lambda c, p=pending: ("timer_irq_in",) if p(c) else ())
        if taken:
            assert r.dmem.get(0x204) == target, f"{what}: mepc {r.dmem.get(0x204)}, expected 0x{target:x}"
            assert r.dmem.get(0x208) == 0x80000007, f"{what}: mcause {r.dmem.get(0x208)}"
            assert 0x200 not in r.dmem, f"{what}: `target` executed although the interrupt was taken"
        else:
            assert 0x204 not in r.dmem, f"{what}: the interrupt was taken (mepc {r.dmem.get(0x204)})"
            assert r.dmem.get(0x200) == 1, f"{what}: main path result {r.dmem.get(0x200)}"


def load_program():
    """lw t0, 0(t0) (rd = rs1: a register write while the load waits would move its own
    address) and lb from another lane, bracketed by minstret reads"""
    a = Asm()
    a.li("t0", 0x100)
    a.li("t1", 0x101)
    a.csrr("s0", "minstret")
    a.label("load")
    a.lw("t0", 0, "t0")
    a.csrr("s1", "minstret")
    a.sub("s1", "s1", "s0")
    a.sw("t0", 0x200, "zero")
    a.sw("s1", 0x204, "zero")
    a.label("load_b")
    a.lb("t2", 0, "t1")
    a.sw("t2", 0x208, "zero")
    a.label("done")
    a.j("done")
    return program_imem(a), a.labels


LOAD_DATA = {0x100: 0x8765A321}


def mem_cycle(trace, *, read=None, write=None):
    """The MEM_WAIT that completes a load of `read` / a store to `write` (no stalls)"""
    if read is not None:
        return next(i for i, c in enumerate(trace) if c.read_req and c.daddr == read)
    return next(i for i, c in enumerate(trace) if c.mem_write and c.waddr == write)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_stalled_load(dut):
    """data_ready low in a load's MEM_WAIT: no mem_read_req, the address stays, garbage on
    data_in isn't written, the right value is on completion, and minstret counts it once"""
    start_clock(dut)
    imem, _ = load_program()
    base = await run(dut, imem, LOAD_DATA)
    assert base.dmem.get(0x200) == 0x8765A321, f"lw result {base.dmem.get(0x200)}"
    # csrr s0 retires after its read, then the lw: two between the reads
    assert base.dmem.get(0x204) == 2, f"minstret delta {base.dmem.get(0x204)}"
    assert base.dmem.get(0x208) == 0xFFFFFFA3, f"lb result {base.dmem.get(0x208)}"
    for addr in (0x100, 0x101):
        start = mem_cycle(base.trace, read=addr)
        for n in (1, 3, 4):
            stalled = await run(dut, imem, LOAD_DATA, data_ready=low_during(start, n))
            check_stalled(base, stalled, start, n, f"load of 0x{addr:x} stalled {n}")
            assert stalled.dmem.get(0x204) == 2, f"minstret counted the stalled load {stalled.dmem.get(0x204)} times"


def store_program():
    a = Asm()
    a.li("t0", 0x11223344)
    a.li("t1", 0x200)
    a.label("store_w")
    a.sw("t0", 0, "t1")
    a.label("store_b")
    a.sb("t0", 5, "t1")  # lane 1 of 0x204
    a.csrr("s0", "minstret")
    a.sw("s0", 0x300, "zero")
    a.label("done")
    a.j("done")
    return program_imem(a), a.labels


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_stalled_store(dut):
    """data_ready low in a store's MEM_WAIT: mem_write, data_waddr, data_out and
    mem_byte_we stay as they are until ready, the write completes once and the CPU leaves
    MEM_WAIT only then"""
    start_clock(dut)
    imem, _ = store_program()
    base = await run(dut, imem)
    assert base.dmem.get(0x200) == 0x11223344 and base.dmem.get(0x204) == 0x4400, base.dmem
    for addr in (0x200, 0x205):
        start = mem_cycle(base.trace, write=addr)
        for n in (1, 2, 4):
            stalled = await run(dut, imem, data_ready=low_during(start, n))
            check_stalled(base, stalled, start, n, f"store to 0x{addr:x} stalled {n}")
            held = sum(1 for c in stalled.trace if c.mem_write and c.waddr == addr)
            assert held == n + 1, f"store to 0x{addr:x}: mem_write for {held} cycles, expected {n + 1}"
            assert sum(1 for w in stalled.writes if w[1] == addr) == 1


def hack_word(lo, hi):
    return (hi << 16) | lo


def hack_program():
    """@5, D=M, @6, M=D, then a jump-to-self: copies M[5] to M[6]"""
    instrs = [
        0x0005,  # @5
        0xFC10,  # D=M
        0x0006,  # @6
        0xE308,  # M=D
        0x0004,  # @4
        0xEA87,  # 0;JMP
    ]
    return {4 * i: hack_word(instrs[2 * i], instrs[2 * i + 1]) for i in range(len(instrs) // 2)}


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_stalled_hack_m_read(dut):
    """Hack: data_ready low in the MEM_WAIT of an M read latches data_in only on
    completion (garbage before it), and a stalled first FETCH (showing an RV32 word)
    doesn't make the mode detector pick RV32"""
    start_clock(dut)
    imem = hack_program()
    data = {5: 0x1234}
    base = await run(dut, imem, data, hack=True)
    assert int(dut.active_mode.value) == 0, "positive control: not in Hack mode"
    assert base.dmem.get(6) == 0x1234, f"M[6] = {base.dmem.get(6)}"
    start = mem_cycle(base.trace, read=5)
    for n in (1, 3):
        stalled = await run(dut, imem, data, data_ready=low_during(start, n), hack=True)
        check_stalled(base, stalled, start, n, f"Hack M read stalled {n}")
        assert stalled.dmem.get(6) == 0x1234
    stalled = await run(dut, imem, data, instr_ready=low_during(0, 2), hack=True)
    assert int(dut.active_mode.value) == 0, "a stalled first FETCH set RV32 mode"
    check_stalled(base, stalled, 0, 2, "Hack first FETCH stalled 2")


def random_program():
    """A loop of dependent loads, stores and ALU operations, ending with minstret"""
    a = Asm()
    a.li("t0", 0x200)
    a.li("t1", 0x11)
    a.li("t2", 6)
    a.label("loop")
    a.sw("t1", 0, "t0")
    a.lw("t3", 0, "t0")
    a.add("t1", "t1", "t3")
    a.sb("t1", 1, "t0")
    a.lbu("t4", 1, "t0")
    a.sub("t1", "t1", "t4")
    a.addi("t0", "t0", 4)
    a.addi("t2", "t2", -1)
    a.bnez("t2", "loop")
    a.csrr("t5", "minstret")
    a.sw("t5", 0, "t0")
    a.label("done")
    a.j("done")
    return program_imem(a), a.labels


def random_ready(rng, cycles):
    """Low on about half the cycles, never more than 3 in a row (like tb_hex_runner)"""
    out, run_len = [], 0
    for _ in range(cycles):
        low = run_len < 3 and rng.random() < 0.5
        run_len = run_len + 1 if low else 0
        out.append(not low)
    return out


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_random_stalls(dut):
    """Random waits on both ports: the same writes in the same order as without waits"""
    start_clock(dut)
    imem, labels = random_program()
    cycles = 700  # the program takes under 200 cycles without waits
    base = await run(dut, imem, max_cycles=cycles)
    done = labels["done"]
    assert base.trace[-1].pc == done and base.writes, "positive control: the program didn't finish"
    for _ in range(3):
        rng = random.Random(random.getrandbits(32))
        ir, dr = random_ready(rng, cycles), random_ready(rng, cycles)
        r = await run(dut, imem, instr_ready=ir.__getitem__, data_ready=dr.__getitem__, max_cycles=cycles)
        assert r.trace[-1].pc == done, "didn't finish within the step cap"
        assert [w[1:] for w in r.writes] == [w[1:] for w in base.writes], "the writes differ from the run without waits"
        for i, c in enumerate(r.trace):
            assert not (c.read_req and not c.data_ready), f"cycle {i}: mem_read_req while data_ready is low"
