# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu CSRs (RISC-V mode) as a program sees them: reset values, WARL masks, every
writable CSR holding its value while the others are written, CSRRS/CSRRC and their
immediate forms, the counters (both halves, the increment after a write) and soft_rst.

rv32i_csrs has its own unit test; these run the same behavior through the whole CPU,
which is what `make mutation MCY_TOP=unified_cpu` measures. Each program checks the
values itself and spins at "ok" (or "bad"; mtvec points there, so a trap fails too).
They use only the CPU's ports, like test_unified_cpu_traps (whose harness they share).
"""

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge
from rv32_asm import CSRS, Asm
from test_unified_cpu_traps import expect_ok, run_program

# (CSR, mask of the writable bits, bits that always read 1)
WRITABLE = (
    ("mstatus", 0x0000_0088, 0x0000_1800),  # MIE/MPIE; MPP reads as M
    ("mie", 0x0000_0888, 0),
    ("mtvec", 0xFFFF_FFFC, 0),
    ("mscratch", 0xFFFF_FFFF, 0),
    ("mepc", 0xFFFF_FFFC, 0),
    ("mcause", 0xFFFF_FFFF, 0),
    ("mtval", 0xFFFF_FFFF, 0),
)
PATTERNS = (0x5555_5555, 0xAAAA_AAAA, 0xFFFF_FFFF, 0x0F0F_F0F0, 0xF0F0_0F0F, 0x3333_CCCC, 0xCCCC_3333)
MISA_READ = (0x301 << 20) | (2 << 12) | (5 << 7) | 0x73
# Few enough instructions to have run since the counter was zero or written
COUNTER_SLACK = 64


def csr_word(funct3: int, csr: str, src: int, rd: int) -> int:
    """A Zicsr instruction by number: src is rs1 or, for the I forms, uimm"""
    return (CSRS[csr] << 20) | (src << 15) | (funct3 << 12) | (rd << 7) | 0x73


def program(body):
    a = Asm()
    a.csrr("t5", "mtvec")  # its reset value, before it is pointed at "bad"
    a.la("t0", "bad")
    a.csrw("mtvec", "t0")
    body(a)
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels


def expect(a, reg, value):
    a.li("t6", value)
    a.bne(reg, "t6", "bad")


def check_reset_values(a):
    """Every CSR as reset leaves it (counters: small; mtvec as program() read it into
    t5); uses t0, t1, t6"""
    for csr in ("mcycle", "minstret"):
        a.csrr("t0", csr)
        a.li("t1", COUNTER_SLACK)
        a.bgeu("t0", "t1", "bad")
    for csr in ("mcycleh", "minstreth", "mip"):
        a.csrr("t0", csr)
        a.bnez("t0", "bad")
    a.bnez("t5", "bad")
    for csr, _, ones in WRITABLE:
        if csr != "mtvec":
            a.csrr("t0", csr)
            expect(a, "t0", ones)
    a.word(MISA_READ)  # csrr t0, misa
    expect(a, "t0", 0x4000_0100)


@cocotb.test()
async def test_csrs_reset_and_hold_masked_writes(dut):
    """Reset values; then three rounds of writing a different pattern to every writable
    CSR and reading them all back (twice, a load in between: a register that flips while
    it holds is caught whatever the hold time's parity). Masks: mstatus MIE/MPIE (MPP
    reads M), mie the three enables, mtvec/mepc 4-byte aligned. mtvec, written last, holds
    each pattern while every other CSR is written."""

    def body(a):
        check_reset_values(a)
        for rnd in range(3):
            values = [PATTERNS[(i + rnd) % len(PATTERNS)] for i in range(len(WRITABLE))]
            for (csr, _, _), v in zip(WRITABLE, values, strict=True):
                if csr == "mtvec":
                    continue  # stays at "bad"
                a.li("t0", v)
                a.csrw(csr, "t0")
            for read in range(2):
                if read:
                    a.lw("zero", 0, "zero")
                for (csr, mask, ones), v in zip(WRITABLE, values, strict=True):
                    if csr == "mtvec":
                        continue
                    a.csrr("t0", csr)
                    expect(a, "t0", (v & mask) | ones)
        # mtvec last (a trap now would land in the pattern, never at "ok"): held across
        # a load and across each write to another CSR, read after every one (a bit that
        # flips per write would be back after an even number)
        for v in PATTERNS:
            a.li("t0", v)
            a.csrw("mtvec", "t0")
            a.csrr("t1", "mtvec")
            a.lw("zero", 0, "zero")
            a.csrr("t2", "mtvec")
            expect(a, "t1", v & 0xFFFF_FFFC)
            expect(a, "t2", v & 0xFFFF_FFFC)
            a.li("t0", ~v & 0xFFFF_FFFF)
            for csr, _, _ in WRITABLE:
                if csr != "mtvec":
                    a.csrw(csr, "t0")
                    a.csrr("t1", "mtvec")
                    expect(a, "t1", v & 0xFFFF_FFFC)
        a.la("t0", "bad")
        a.csrw("mtvec", "t0")

    await expect_ok(dut, program(body), max_cycles=8000)


@cocotb.test()
async def test_csr_set_clear_forms(dut):
    """CSRRS/CSRRC (register and immediate forms) return the old value and change only
    the given bits; CSRRWI writes uimm"""
    t0, t1 = 5, 6

    def body(a):
        a.li("t0", 0xFFFF_FFFF)
        a.csrw("mscratch", "t0")
        a.li("t0", 0x0000_0400)
        a.word(csr_word(0b011, "mscratch", t0, t1))  # csrrc t1, mscratch, t0
        expect(a, "t1", 0xFFFF_FFFF)
        a.csrr("t1", "mscratch")
        expect(a, "t1", 0xFFFF_FBFF)  # bit 10 cleared, bit 11 and the rest kept
        a.li("t0", 0x0000_0800)
        a.word(csr_word(0b011, "mscratch", t0, t1))
        a.csrr("t1", "mscratch")
        expect(a, "t1", 0xFFFF_F3FF)
        a.word(csr_word(0b111, "mscratch", 0x13, t1))  # csrrci t1, mscratch, 0x13
        expect(a, "t1", 0xFFFF_F3FF)
        a.csrr("t1", "mscratch")
        expect(a, "t1", 0xFFFF_F3EC)
        a.word(csr_word(0b101, "mscratch", 0x0A, t1))  # csrrwi t1, mscratch, 0x0A
        expect(a, "t1", 0xFFFF_F3EC)
        a.word(csr_word(0b110, "mscratch", 0x11, t1))  # csrrsi t1, mscratch, 0x11
        expect(a, "t1", 0x0000_000A)
        a.li("t0", 0x0080_0400)
        a.word(csr_word(0b010, "mscratch", t0, t1))  # csrrs t1, mscratch, t0
        expect(a, "t1", 0x0000_001B)
        a.csrr("t1", "mscratch")
        expect(a, "t1", 0x0080_041B)
        # mie through CSRRC: MEIE (bit 11) survives clearing MTIE
        a.li("t0", 0x888)
        a.csrw("mie", "t0")
        a.li("t0", 0x80)
        a.word(csr_word(0b011, "mie", t0, 0))  # csrc mie, t0
        a.csrr("t1", "mie")
        expect(a, "t1", 0x808)

    await expect_ok(dut, program(body), max_cycles=1000)


def counter_checks(a, lo, hi, user_lo, user_hi):
    """Write lo with patterns: what the next instruction reads is the value plus the same
    small increment each time. Then hi: read back exactly (twice, a load in between), also
    through the user-level aliases. Uses t0-t2, s2, t6."""
    for n, v in enumerate((0, 0x5555_5555, 0xAAAA_AAAA, 0x0F0F_F0F0)):
        a.li("t0", v)
        a.csrw(lo, "t0")
        a.csrr("t1", lo)
        a.sub("t1", "t1", "t0")
        if n == 0:
            a.mv("s2", "t1")
            a.li("t2", COUNTER_SLACK)
            a.bgeu("s2", "t2", "bad")
        else:
            a.bne("t1", "s2", "bad")
    a.csrw(lo, "zero")  # no carry into hi below
    for v in (0x5555_5555, 0xAAAA_AAAA, 0xFFFF_FFFF, 0):
        a.li("t0", v)
        a.csrw(hi, "t0")
        a.csrr("t1", hi)
        a.bne("t1", "t0", "bad")
        a.lw("zero", 0, "zero")
        a.csrr("t1", hi)
        a.bne("t1", "t0", "bad")
        a.csrr("t1", user_hi)
        a.bne("t1", "t0", "bad")
    a.csrr("t1", lo)
    a.csrr("t2", user_lo)
    a.bltu("t2", "t1", "bad")  # the alias reads the same counter, a little later
    a.li("t0", 4 * COUNTER_SLACK)
    a.bgeu("t2", "t0", "bad")


@cocotb.test()
async def test_counters_write_and_read_back(dut):
    """mcycle/minstret: both halves written and read back (and through cycle/instret);
    two reads in a row of mcycle differ by the same amount each time"""

    def body(a):
        counter_checks(a, "mcycle", "mcycleh", "cycle", "cycleh")
        counter_checks(a, "minstret", "minstreth", "instret", "instreth")
        for n, v in enumerate((0x5555_5555, 0xAAAA_AAA8, 0x0000_0000)):
            a.li("t0", v)
            a.csrw("mcycle", "t0")
            a.csrr("t1", "mcycle")
            a.csrr("t2", "mcycle")
            a.sub("t1", "t2", "t1")
            if n == 0:
                a.mv("s3", "t1")
                a.beqz("s3", "bad")
            else:
                a.bne("t1", "s3", "bad")

    await expect_ok(dut, program(body), max_cycles=3000)


def half_independence_checks(a, lo, hi):
    """With a pattern in one half of the counter, the other half read twice an odd number
    of cycles apart (an ALU or CSR instruction takes 2 cycles, a load 3): a bit of one half
    that a bit of the other disturbs toggles while it counts or holds, so one of the two
    reads shows it. Uses t0-t2, s4, t6."""
    for n, h in enumerate((0, 0x5555_5555, 0xAAAA_AAAA)):
        a.li("t0", h)
        a.csrw(hi, "t0")
        a.csrw(lo, "zero")
        a.csrr("t1", lo)
        a.lw("zero", 0, "zero")
        a.csrr("t2", lo)
        a.li("t6", COUNTER_SLACK)
        a.bgeu("t1", "t6", "bad")
        a.sub("t2", "t2", "t1")
        if n == 0:
            a.mv("s4", "t2")
        else:
            a.bne("t2", "s4", "bad")
        a.csrr("t1", hi)
        expect(a, "t1", h)
    for v, h in ((0x5555_5555, 0xAAAA_AAAA), (0xAAAA_AAAA, 0x5555_5555)):  # no carry into hi
        a.li("t0", v)
        a.csrw(lo, "t0")
        a.li("t0", h)
        a.csrw(hi, "t0")
        a.csrr("t1", hi)
        expect(a, "t1", h)
        a.lw("zero", 0, "zero")
        a.csrr("t1", hi)
        expect(a, "t1", h)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def test_counter_halves_independent(dut):
    """mcycle/minstret: one half holds its value whatever the other half holds (mutants
    that flipped minstret[12] with minstret[47], mcycle[34] with mcycle[18] survived the
    tests above, which read each half only while the other was zero)"""

    def body(a):
        half_independence_checks(a, "mcycle", "mcycleh")
        half_independence_checks(a, "minstret", "minstreth")

    await expect_ok(dut, program(body), max_cycles=2000)


async def soft_reset_at(dut, pc, pulses):
    """Pulse soft_rst for one cycle each time the CPU is at `pc`, `pulses` times"""
    for _ in range(pulses):
        while True:
            await FallingEdge(dut.clk)
            if str(dut.rst.value) == "1" and str(dut.pc_out.value) == format(pc, "032b"):
                break
        dut.soft_rst.value = 1
        await FallingEdge(dut.clk)
        dut.soft_rst.value = 0


@cocotb.test()
async def test_soft_reset_clears_csrs(dut):
    """soft_rst restarts the program and returns every CSR to its reset value: the
    program checks them, sets every writable CSR and both counter halves to all ones,
    and waits at "armed" for the soft reset; the second pass checks them again"""

    def body(a):
        check_reset_values(a)
        a.label("checked")
        a.li("t0", 0xFFFF_FFFF)
        for csr in ("mstatus", "mie", "mscratch", "mepc", "mcause", "mtval", "mcycleh", "minstreth", "mtvec"):
            a.csrw(csr, "t0")
        a.label("armed")
        a.j("armed")

    words, labels = program(body)
    cocotb.start_soon(soft_reset_at(dut, labels["armed"], 1))
    pcs, _ = await run_program(dut, words, max_cycles=1500)
    last = [hex(p) for p in pcs[-4:]]
    assert labels["bad"] not in pcs, f"a CSR check failed; last PCs {last}"
    first_armed = pcs.index(labels["armed"])
    after = pcs[first_armed:]
    assert labels["checked"] in after, f"the second pass never got past the checks; last PCs {last}"
    assert labels["armed"] in after[after.index(labels["checked"]) :], f"never re-armed; last PCs {last}"


MTIME = 0x8421_5A3C_7BDE_A5C3
MTIME_SWITCH = 200  # cycles after reset: mtime_in becomes its complement


async def drive_mtime(dut):
    """mtime_in = MTIME once rst is released (run_program zeroes it before the reset),
    then ~MTIME after MTIME_SWITCH cycles"""
    await FallingEdge(dut.clk)  # rst may still read 1 from the previous test until run_program resets
    while str(dut.rst.value) != "1":
        await FallingEdge(dut.clk)
    dut.mtime_in.value = MTIME
    await ClockCycles(dut.clk, MTIME_SWITCH)
    dut.mtime_in.value = ~MTIME & (1 << 64) - 1


@cocotb.test()
async def test_time_csrs_read_mtime(dut):
    """time and timeh read the two halves of mtime_in, every bit both ways (the value,
    then its complement)"""

    def body(a):
        for n, value in enumerate((MTIME, ~MTIME & (1 << 64) - 1)):
            a.label(f"wait{n}")
            a.csrr("t0", "time")
            a.li("t6", value & 0xFFFF_FFFF)
            a.bne("t0", "t6", f"wait{n}")
            a.csrr("t0", "timeh")
            expect(a, "t0", value >> 32)

    words, labels = program(body)
    cocotb.start_soon(drive_mtime(dut))
    pcs, _ = await run_program(dut, words, max_cycles=MTIME_SWITCH + 300)
    last = [hex(p) for p in pcs[-4:]]
    assert labels["bad"] not in pcs, f"timeh read wrong; last PCs {last}"
    assert labels["ok"] in pcs, f"never read both values; last PCs {last}"
