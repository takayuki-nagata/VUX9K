# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Programs for the RTL <-> emulator lockstep check (sim/integration/lockstep_programs.py).

Pure Python, shared by both sides: test_soc_lockstep.py runs each on the RTL with
tb_soc_top's trace on (build/sim/<sim>/tb_soc_top/run_test_soc_lockstep/lockstep.trace),
and sim/emu/test_lockstep.py runs the same setup on the emulator and compares every
instruction (fetch cycle, ISA, PC, register writes, bus writes, traps) and every
UART TX byte with its cycle.

A program is the power-on state (firmware preload from build/firmware, words over
I-RAM, SD sectors; the words of the wait-state window the programs use (XMEM_USED) zeroed,
as the emulator starts it) plus host
stimulus at exact cycles (cycle 0 = first cycle out of reset): UART bytes sent back to
back from a cycle, and S2 button levels.
"""

import os
import random
import struct
import sys
from dataclasses import dataclass, field

from rv32_asm import Asm, b_type, i_type, j_type, r_type

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UART_BIT = 156  # soc_pkg::UART_CNT: 18 MHz / 115200
UART_FRAME = 10 * UART_BIT
XMEM = 0x8000_0000  # the wait-state window: soc_pkg::RV_REGION_XMEM, 8 MB of PSRAM
XMEM_BYTES = 0x80_0000  # soc_pkg::XMEM_WORDS * 4: the window repeats every 8 MB of region 8
XMEM_WAIT = 35  # soc_pkg::XMEM_READ_LATENCY: the longest wait of one access


@dataclass
class Program:
    name: str
    cycles: int  # run length, from cycle 0
    imem_words: list | None = None  # over the firmware preload, from word 0
    uart_rx: list = field(default_factory=list)  # (cycle, bytes): start bits back to back from cycle
    button: list = field(default_factory=list)  # (cycle, pressed) S2 pin level from cycle
    sd_sectors: dict = field(default_factory=dict)  # lba -> bytes (after the MBR in sector 0)
    slow: bool = False  # only in the long run (make sim-lockstep-slow)
    needs: list = field(default_factory=list)  # build outputs its setup is made from


def _csr_read(rd, csr):
    return (csr << 20) | (2 << 12) | (rd << 7) | 0x73  # csrrs rd, csr, x0


# --- constrained-random RV32I ----------------------------------------------------------
# x2 (sp) = base for loads/stores (D-RAM, or the window), x30 = timer base, x31 = loop
# counter; the rest are random. No traps: aligned accesses only, branches only forward or
# counted loops.
_R_OPS = [(0, 0), (0x20, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0x20, 5), (0, 6), (0, 7)]
_I_OPS = [0, 2, 3, 4, 6, 7]  # addi slti sltiu xori ori andi
_DEST = [f"x{i}" for i in range(1, 30) if i != 2]


def random_rv(seed: int, blocks: int = 300, base: int = 0x2000_0800) -> list:
    rnd = random.Random(seed)
    a = Asm()
    a.li("sp", base)
    a.li("x30", 0x40001000)
    for r in _DEST:
        a.li(r, rnd.getrandbits(32))

    def reg():
        return rnd.choice(_DEST)

    def alu():
        k = rnd.randrange(5)
        if k == 0:
            f7, f3 = rnd.choice(_R_OPS)
            a.word(r_type(f7, reg(), reg(), f3, reg()))
        elif k == 1:
            a.word(i_type(rnd.randrange(-2048, 2048), reg(), rnd.choice(_I_OPS), reg(), 0x13))
        elif k == 2:  # slli/srli/srai
            f3, f7 = rnd.choice([(1, 0), (5, 0), (5, 0x20)])
            a.word((f7 << 25) | (rnd.randrange(32) << 20) | i_type(0, reg(), f3, reg(), 0x13))
        elif k == 3:
            a.lui(reg(), rnd.getrandbits(20))
        else:
            a.auipc(reg(), rnd.getrandbits(20))

    for b in range(blocks):
        k = rnd.randrange(10)
        if k < 4:
            alu()
        elif k < 6:  # load/store around sp
            size = rnd.choice([1, 2, 4])
            off = rnd.randrange(-512, 512) & ~(size - 1)
            if rnd.random() < 0.5:
                a.word(i_type(off, "sp", {1: rnd.choice([0, 4]), 2: rnd.choice([1, 5]), 4: 2}[size], reg(), 0x03))
            else:
                getattr(a, {1: "sb", 2: "sh", 4: "sw"}[size])(reg(), off, "sp")
        elif k == 6:  # forward branch over a few instructions
            f3 = rnd.choice([0, 1, 4, 5, 6, 7])
            lbl = f"f{b}"
            rs1, rs2 = reg(), reg()
            a._emit(lambda pc, labels, f3=f3, rs1=rs1, rs2=rs2, lbl=lbl: b_type(labels[lbl] - pc, rs2, rs1, f3))
            for _ in range(rnd.randrange(1, 4)):
                alu()
            a.label(lbl)
        elif k == 7:  # counted loop
            lbl = f"l{b}"
            a.li("x31", rnd.randrange(1, 6))
            a.label(lbl)
            for _ in range(rnd.randrange(1, 4)):
                alu()
            a.addi("x31", "x31", -1)
            a.bnez("x31", lbl)
        elif k == 8:  # jal/jalr forward
            lbl = f"j{b}"
            if rnd.random() < 0.5:
                a.jal(reg(), lbl)
            else:
                t = reg()
                a.la(t, lbl)
                a.jalr(reg(), t, 0)
            alu()
            a.label(lbl)
        else:  # counters and mtime
            if rnd.random() < 0.5:
                a.word(_csr_read(int(reg()[1:]), rnd.choice([0xB00, 0xB02, 0xC00, 0xC01, 0xC02, 0xB80, 0xC81])))
            else:
                a.lw(reg(), rnd.choice([0, 4, 8]), "x30")
    a.label("end")
    a.j("end")
    words = a.assemble()
    assert len(words) <= 0x3800 // 4, f"random program of {len(words)} words over 14 KB"
    return words


# --- the wait-state window ----------------------------------------------------------------
XMEM_IRQS = 4  # timer interrupts taken while running from the window


# Word-address bits the xmem program sets one at a time (bits 0-8 are within its first 2 KB)
XMEM_ADDR_LINES = range(9, 21)
# Byte offsets in the window that the window programs read or write, as [start, end) ranges:
# test_soc_lockstep zeroes these words of the RTL's PSRAM word model before each program, as
# the emulator starts its window zeroed (never all 8 MB: one VPI write per word)
XMEM_USED = [(0, 0x800), (0x7F_F840, 0x7F_F848)] + [(4 << b, (4 << b) + 4) for b in XMEM_ADDR_LINES]
# The first stage's .bss and stack (fw_common::map::S1_RAM_BASE, 64 KB below the second
# stage) and the start of the second stage's window (STAGE2_BASE), for the s1_* programs
STAGE2_BASE = 0x8070_0000
XMEM_USED += [(0x6F_0000, 0x6F_0100), (0x6F_E000, 0x70_0000), (0x70_0000, 0x70_0100)]
# s2_launch's two parts and its RV32 program (S2L_CODE, S2L_COPY, S2L_APP, S2L_BYTES each)
XMEM_USED += [(0x1000, 0x1200), (0x1800, 0x1A00), (0x2000, 0x2200)]
# xcache's routines: A, and B one instruction-cache size (4 KB) above it on the same lines
XCACHE_A, XCACHE_B = 0x600, 0x1600
XMEM_USED += [(XCACHE_B, XCACHE_B + 0x40)]


def xmem() -> list:
    """The window at 0x8000_0000: stores and loads of every size, through aliases (8 MB up,
    the top of region 8, and 0x8080_0020, which would be the PSRAM's CR0 if the controller's
    register space were reachable); a store to each high word-address line, all read back; a
    routine copied there (fence.i) and run, which sends a UART byte, reads the timer and
    mcycle and accesses the window itself; then a loop in the window, nearly all of whose
    cycles are fetch waits, until four timer interrupts (mtimecmp a different distance
    ahead each time) have been taken: each becomes pending while a fetch waits, so its
    sampling point (the fetch's last cycle) decides which instruction takes it."""
    a = Asm()
    a.li("s0", XMEM)
    a.li("s1", 0x4000_0000)  # UART
    a.li("s2", 0x4000_1000)  # timer
    a.li("s10", 0x2000_0800)  # D-RAM
    # Stores of every size, loads back, an alias 8 MB up and one at the top of the region
    a.li("t0", 0x1122_3344)
    a.sw("t0", 0x40, "s0")
    a.li("t1", 0xA5)
    a.sb("t1", 0x41, "s0")
    a.li("t1", 0xBEEF)
    a.sh("t1", 0x46, "s0")
    a.lw("t2", 0x40, "s0")
    a.lb("t3", 0x41, "s0")
    a.lbu("t4", 0x41, "s0")
    a.lh("t5", 0x46, "s0")
    a.word(i_type(0x46, "s0", 5, "t6", 0x03))  # lhu
    a.li("s3", XMEM + XMEM_BYTES)
    a.lw("t2", 0x40, "s3")
    a.li("s4", 0x8FFF_F800)  # the window's last 2 KB, 8 MB below
    a.sw("t0", 0x44, "s4")
    a.li("t1", XMEM + XMEM_BYTES - 0x800)
    a.lw("t3", 0x44, "t1")
    a.sw("t3", 0x40, "s10")  # a D-RAM store between window accesses
    # Not the register space: 0x8080_0020 is the word at 0x8000_0020
    a.li("t1", 0xC0DE_0020)
    a.sw("t1", 0x20, "s3")
    a.lw("t2", 0x20, "s0")
    # Each high word-address line on its own, then all read back
    for b in XMEM_ADDR_LINES:
        a.li("t1", XMEM + (4 << b))
        a.li("t0", 0x5A00_0000 | (b << 8) | b)
        a.sw("t0", 0, "t1")
    for b in XMEM_ADDR_LINES:
        a.li("t1", XMEM + (4 << b))
        a.lw("t2", 0, "t1")

    # The routine, assembled at its window address
    r = Asm(origin=XMEM + 0x100)
    r.sb("a0", 0, "s1")  # UART TX
    r.lw("t0", 0, "s2")  # mtime, read in the EXECUTE cycle after the wait
    r.csrr("t1", "mcycle")
    r.lw("t2", 0x40, "s0")  # window load from window code
    r.sw("t1", 0x48, "s0")
    r.jalr("zero", "ra", 0)
    r.label("spin")  # the interrupt loop: count until the handler has run XMEM_IRQS times
    r.addi("s6", "s6", 1)
    r.li("t1", XMEM_IRQS)
    r.bltu("s5", "t1", "spin")
    r.jalr("zero", "ra", 0)
    routine = r.assemble()
    for k, w in enumerate(routine):
        a.li("t0", w)
        a.sw("t0", 0x100 + 4 * k, "s0")
    a.word(0x0000_100F)  # fence.i
    a.li("a0", ord("W"))
    a.li("t0", XMEM + 0x100)
    a.jalr("ra", "t0", 0)
    a.lw("t4", 0x48, "s0")

    # Timer interrupts while the window loop runs
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s7", 211)  # first distance in cycles; the handler adds 37 per interrupt
    a.call("arm")
    a.li("t0", 0x80)  # MTIE
    a.csrw("mie", "t0")
    a.li("t0", 8)
    a.csrs("mstatus", "t0")
    a.li("t0", r.labels["spin"])
    a.jalr("ra", "t0", 0)
    a.label("done")
    a.j("done")

    a.label("arm")  # mtimecmp = mtime + s7
    a.li("t0", -1)
    a.sw("t0", 0xC, "s2")
    a.lw("t0", 0, "s2")
    a.add("t0", "t0", "s7")
    a.sw("t0", 8, "s2")
    a.sw("zero", 0xC, "s2")
    a.ret()

    a.label("handler")  # count, re-arm a little further away, back into the window
    a.csrr("s8", "mepc")
    a.addi("s5", "s5", 1)
    a.addi("s7", "s7", 37)
    a.mv("s9", "ra")
    a.call("arm")
    a.mv("ra", "s9")
    a.mret()
    words = a.assemble()
    assert len(words) <= 0x3800 // 4
    return words


XCACHE_LOOPS = 40  # first pass of routine A's loop
XCACHE_IRQ_LOOPS = 400  # the last pass, with a timer interrupt in it


def xcache() -> list:
    """The window's instruction cache (soc_icache): routine A, a counted loop that hits from
    its second pass on, then a store to the next instruction's word (no fence.i: the store
    invalidates the line, in the very cycle that line is read ahead, which the cache turns
    into a miss) and a store to the next fetch's line under another tag (the data stays,
    the line goes); routine B, 4 KB above A on the same lines, called alternately with A so
    each evicts the other; A through the window's 8 MB alias (the same lines: a hit); and a
    last long pass of A's loop, all hits, during which a timer interrupt is taken."""
    a = Asm()
    a.li("s0", XMEM)
    a.li("s2", 0x4000_1000)  # timer

    r = Asm(origin=XMEM + XCACHE_A)
    r.label("loop")
    r.addi("s6", "s6", 1)
    r.bltu("s6", "s7", "loop")
    r.label("smc")
    r.sw("t2", 4, "s5")  # s5 = smc: rewrites the next word before it is fetched
    r.addi("a1", "zero", 1)  # becomes `addi a1, zero, 2`
    r.label("other")
    r.sw("zero", 12, "s11")  # s11 = smc + 4 KB: 4 KB above the next fetch (other + 4), its line
    r.addi("a2", "a2", 1)
    r.jalr("zero", "ra", 0)
    ra_words = r.assemble()
    smc = r.labels["smc"]
    assert r.labels["other"] + 4 + 0x1000 == smc + 0x1000 + 12
    assert smc + 0x1000 + 12 >= XMEM + XCACHE_B + 8, "the store above must miss routine B"
    rb = Asm(origin=XMEM + XCACHE_B)
    rb.addi("s8", "s8", 1)
    rb.jalr("zero", "ra", 0)
    for base, words in ((XCACHE_A, ra_words), (XCACHE_B, rb.assemble())):
        a.li("t1", XMEM + base)
        for k, w in enumerate(words):
            a.li("t0", w)
            a.sw("t0", 4 * k, "t1")
    t = Asm()
    t.addi("a1", "zero", 2)
    a.li("t2", t.assemble()[0])
    a.li("s5", smc)
    a.li("s11", smc + 0x1000)
    a.li("s7", XCACHE_LOOPS)
    a.li("s3", XMEM + XCACHE_A)
    a.li("s4", XMEM + XCACHE_B)
    a.jalr("ra", "s3", 0)  # A: cold, then hits
    for _ in range(3):  # B and A on the same lines: misses
        a.jalr("ra", "s4", 0)
        a.mv("s6", "zero")
        a.li("s7", 2)
        a.jalr("ra", "s3", 0)
    a.li("t0", XMEM + XMEM_BYTES + XCACHE_A)  # A through the alias: its lines, hits
    a.mv("s6", "zero")
    a.jalr("ra", "t0", 0)

    # The last pass with a timer interrupt
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("t0", -1)
    a.sw("t0", 0xC, "s2")
    a.lw("t0", 0, "s2")
    a.addi("t0", "t0", 523)
    a.sw("t0", 8, "s2")
    a.sw("zero", 0xC, "s2")
    a.li("t0", 0x80)  # MTIE
    a.csrw("mie", "t0")
    a.li("t0", 8)
    a.csrs("mstatus", "t0")
    a.mv("s6", "zero")
    a.li("s7", XCACHE_IRQ_LOOPS)
    a.jalr("ra", "s3", 0)
    a.label("done")
    a.j("done")

    a.label("handler")  # count, disarm, back into the window
    a.csrr("s10", "mepc")
    a.addi("s9", "s9", 1)
    a.li("t0", -1)
    a.sw("t0", 0xC, "s2")
    a.mret()
    words = a.assemble()
    assert len(words) <= 0x3800 // 4
    return words


# --- traps, CSRs, interrupts, UART, soft reset ----------------------------------------
TRAPS_RX_CYCLE = 1500  # the UART byte for the RX interrupt starts here


def traps() -> list:
    a = Asm()
    # Second pass (after the soft reset at the end): x5 survives, the CSRs don't
    a.bnez("x5", "second")
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("sp", 0x20000800)
    a.li("s1", 0x40000000)  # UART
    a.li("s2", 0x40001000)  # timer
    # Exceptions: the handler adds 4 to mepc and keeps mcause in s0
    a.ecall()
    a.ebreak()
    a.word(0)  # illegal
    a.word(0xFFFFFFFF)  # illegal
    a.word(0x7C002073)  # csrrs x0, 0x7C0 (nonexistent CSR): illegal
    a.word(0xC0001073)  # csrrw x0, cycle: write to a read-only CSR
    a.lw("t1", 1, "sp")  # misaligned load
    a.sh("t1", 3, "sp")  # misaligned store
    a.la("t1", "after_ma")
    a.jalr("ra", "t1", 2)  # misaligned jump target
    a.label("after_ma")
    a.nop()
    a.lw("t1", 0, "zero")  # load from I-RAM
    a.lbu("t1", 4, "s1")  # UART status via a byte load
    # CSRs
    a.li("t1", 0x12345678)
    a.csrw("mscratch", "t1")
    a.csrr("t2", "mscratch")
    a.csrw("mcycle", "zero")
    a.csrr("t3", "mcycle")
    a.csrw("minstret", "zero")
    a.csrr("t4", "minstret")
    a.csrr("t5", "mstatus")
    a.csrr("t6", "mip")
    a.word((0x320 << 20) | (6 << 15) | (1 << 12) | 0x73)  # csrw mcountinhibit, t1: ignored
    a.word((0x33F << 20) | (6 << 15) | (1 << 12) | 0x73)  # csrw mhpmevent31, t1: ignored
    a.word(_csr_read(7, 0x320))  # mcountinhibit, mhpmcounter3, mhpmcounter31h: read 0
    a.word(_csr_read(28, 0xB03))
    a.word(_csr_read(29, 0xB9F))
    # UART TX: a few bytes, then poll tx_full
    for ch in b"Hi!\n":
        a.li("t1", ch)
        a.sw("t1", 0, "s1")
    a.lw("t1", 4, "s1")
    # Timer interrupt: mtimecmp = mtime + 200
    a.lw("t1", 0, "s2")
    a.addi("t1", "t1", 200)
    a.sw("zero", 0xC, "s2")
    a.sw("t1", 8, "s2")
    a.li("t1", 0x880)  # MEIE | MTIE
    a.csrw("mie", "t1")
    a.li("t1", 8)
    a.csrs("mstatus", "t1")
    # Wait for both interrupts (the handler counts them in s3)
    a.label("wait")
    a.li("t1", 2)
    a.bltu("s3", "t1", "wait")
    # Soft reset with auto-detected ISA (x5 marks the second pass)
    a.li("x5", 1)
    a.li("t1", 0x40003000)
    a.li("t2", 0xA55A)
    a.sw("t2", 0xC, "t1")
    a.label("spin_reset")
    a.j("spin_reset")
    a.label("second")
    a.csrr("t1", "mtvec")
    a.csrr("t2", "mcycle")
    a.label("done")
    a.j("done")

    a.label("handler")
    a.csrr("s0", "mcause")
    a.csrr("s4", "mtval")
    a.bltu("s0", "zero", "handler")  # never taken (unsigned < 0): exercises a branch here
    a.li("t0", 0x80000000)
    a.bgeu("s0", "t0", "interrupt")
    a.csrr("t0", "mepc")
    a.addi("t0", "t0", 4)
    a.csrw("mepc", "t0")
    a.mret()
    a.label("interrupt")
    a.addi("s3", "s3", 1)
    a.li("t0", 0x8000000B)
    a.beq("s0", "t0", "ext")
    a.li("t0", -1)
    a.sw("t0", 0xC, "s2")  # timer: mtimecmp hi = max
    a.mret()
    a.label("ext")
    a.lw("s5", 0, "s1")  # UART RX: pop the byte
    a.mret()
    return a.assemble()


def hack_soft_reset(key_by_addition: bool = True) -> list:
    """Hack: print 'R', store the soft-reset key to GPIO 0x600C, spin. Hack registers are
    32 bits wide, so 0x255A + 0x4000 + 0x4000 is 0x0000_A55A, a key the GPIO accepts, and
    the program restarts from 0 (ISA detected again); 0xFFFF_A55A from !0x5AA5 is not."""
    import hack_asm

    a = hack_asm.Asm()
    a.at(ord("R"))
    a.c("D=A")
    a.at(0x6000)  # UART data
    a.c("M=D")
    if key_by_addition:
        a.at(0x255A)
        a.c("D=A")
        a.at(0x4000)
        a.c("D=D+A")
        a.at(0x4000)
        a.c("D=D+A")
    else:
        a.at(0x5AA5)
        a.c("D=!A")
    a.at(0x600C)  # GPIO soft reset
    a.c("M=D")
    a.label("spin")
    a.at("spin")
    a.c("0;JMP")
    return a.assemble()


BOOT_MODE_CODE = 0x400  # boot_mode_hack's RV32 code, clear of the Hack program it writes at 0


def boot_mode_hack() -> list:
    """RV32 writes a Hack program starting with `@0` over I-RAM word 0, gives Hack in GPIO
    0x8 (read back), and soft-resets: the CPU must start it in Hack mode, which prints 'H'.
    Without the mode the first-instruction guess can't tell `@0` from RV32 (README, "ISA
    selection"); a loader gives it for every program it starts."""
    import hack_asm

    h = hack_asm.Asm()
    h.at(0)  # first instruction 0x0000
    h.c("D=A")
    h.at(ord("H"))
    h.c("D=D+A")
    h.at(0x6000)  # UART data
    h.c("M=D")
    h.label("halt")
    h.at("halt")
    h.c("0;JMP")

    a = Asm(origin=BOOT_MODE_CODE)
    for i, w in enumerate(h.assemble()):
        a.li("t0", w)
        a.sw("t0", 4 * i, "zero")  # I-RAM word i
    a.lui("s1", 0x40003)  # GPIO
    a.addi("t0", "zero", 0x100)  # valid, Hack
    a.sw("t0", 8, "s1")
    a.lw("s2", 8, "s1")
    a.lui("t0", 0xA)
    a.addi("t0", "t0", 0x55A)  # SOFT_RESET_KEY's low half (0x0000_A55A)
    a.sw("t0", 0xC, "s1")
    a.label("spin")
    a.j("spin")

    return [j_type(BOOT_MODE_CODE, "zero")] + [0] * (BOOT_MODE_CODE // 4 - 1) + a.assemble()


# --- the second stage's launcher, by hand: code in the PSRAM starting code -----------------
S2L_CODE = XMEM + 0x1000  # the launcher's first part, as the second stage's code would run
S2L_COPY = XMEM + 0x1800  # where the first part copies the second (fence.i, then a jump)
S2L_APP = XMEM + 0x2000  # where the second part copies an RV32 program (the PSRAM target)
S2L_BYTES = 0x200  # room for each part and the RV32 program (XMEM_USED)
# 'P' starts at cycle ~11,200 (Hack: ~9,800), 'L' after the jump at ~13,900 ('K' after the soft reset ~11,400)
S2_LAUNCH_CYCLES = 18_000


def _put_char(a, ch: int, tag: str):
    """Send ch once the TX FIFO has room (s1 = the UART)."""
    a.label(tag)
    a.lw("t0", 4, "s1")
    a.andi("t0", "t0", 2)  # TX full
    a.bnez("t0", tag)
    a.li("t0", ch)
    a.sb("t0", 0, "s1")


def s2_launch(hack: bool = False) -> list:
    """What the second stage's `run` does, hand-assembled: I-RAM code writes the launcher
    into the PSRAM (fence.i) and jumps there; the launcher copies its second part to
    another PSRAM address word by word (PSRAM loads and stores from PSRAM code), fence.i,
    jumps there; that part prints 'P', then starts one of fw_common::launch's targets.
    RV32 (the PSRAM target): it copies the program to S2L_APP word by word (as `run` loads
    it in place), sets a0-a2, fence.i and jumps there; the program prints 'L'. Hack: it
    copies the program into I-RAM word by word (start_hack), gives Hack (0x100) in GPIO 0x8
    (read back) and soft-resets: the CPU starts the program in Hack mode, which prints
    'K'."""
    if hack:
        import hack_asm

        h = hack_asm.Asm()
        h.at(ord("K"))
        h.c("D=A")
        h.at(0x6000)  # UART data
        h.c("M=D")
        h.label("halt")
        h.at("halt")
        h.c("0;JMP")
        target = h.assemble()
    else:
        t = Asm(origin=S2L_APP)
        t.lui("s1", 0x40000)
        _put_char(t, ord("L"), "w")
        t.label("halt")
        t.j("halt")
        target = t.assemble()

    # The second part, run at S2L_COPY, with the program it copies (into I-RAM, or to
    # S2L_APP) after it
    s = Asm(origin=S2L_COPY)
    _put_char(s, ord("P"), "w")
    s.la("a0", "target")
    s.li("a1", 0 if hack else S2L_APP)
    s.li("a2", len(target))
    s.label("copy")
    s.lw("t0", 0, "a0")
    s.sw("t0", 0, "a1")
    s.addi("a0", "a0", 4)
    s.addi("a1", "a1", 4)
    s.addi("a2", "a2", -1)
    s.bnez("a2", "copy")
    if hack:
        s.lui("s2", 0x40003)  # GPIO
        s.li("t0", 0x100)
        s.sw("t0", 8, "s2")
        s.lw("s3", 8, "s2")
        s.lui("t0", 0xA)
        s.addi("t0", "t0", 0x55A)  # SOFT_RESET_KEY's low half (0x0000_A55A)
        s.sw("t0", 0xC, "s2")
    else:
        # a0-a2 as the first stage hands them over (SDHC, partition LBA, its version)
        s.li("a0", 1)
        s.li("a1", 0x800)
        s.li("a2", 1)
        s.word(0x0000_100F)  # fence.i
        s.li("t0", S2L_APP)
        s.jalr("zero", "t0", 0)
    s.label("spin")
    s.j("spin")
    s.label("target")
    for w in target:
        s.word(w)
    second = s.assemble()

    # The first part, run at S2L_CODE, with the second part after it
    r = Asm(origin=S2L_CODE)
    r.la("a0", "second")
    r.li("a1", S2L_COPY)
    r.li("a2", len(second))
    r.label("copy")
    r.lw("t0", 0, "a0")
    r.sw("t0", 0, "a1")
    r.addi("a0", "a0", 4)
    r.addi("a1", "a1", 4)
    r.addi("a2", "a2", -1)
    r.bnez("a2", "copy")
    r.word(0x0000_100F)  # fence.i
    r.li("t0", S2L_COPY)
    r.jalr("zero", "t0", 0)
    r.label("second")
    for w in second:
        r.word(w)
    first = r.assemble()
    assert 4 * len(first) <= S2L_BYTES and 4 * len(second) <= S2L_BYTES and 4 * len(target) <= S2L_BYTES

    # From I-RAM: the first part into the PSRAM, fence.i, a jump there
    a = Asm()
    a.li("s1", 0x4000_0000)  # UART, for both parts
    a.li("s0", S2L_CODE)
    for k, w in enumerate(first):
        a.li("t0", w)
        a.sw("t0", 4 * k, "s0")
    a.word(0x0000_100F)  # fence.i
    a.jalr("zero", "s0", 0)
    words = a.assemble()
    assert len(words) <= 0x3800 // 4
    return words


# --- SD SPI master: divider register --------------------------------------------------
def sd_clkdiv() -> list:
    """SD SPI bytes at several dividers (reset 23, 0 -> 3, 5, 255, 2 -> 3): CMD0 and CMD8
    with their answers, the shift register and busy read on every poll mid-transfer,
    and a divider write while busy (ignored)."""
    a = Asm()
    a.li("s1", 0x40002000)
    a.li("s2", 23)
    a.lw("t2", 0xC, "s1")
    a.sw("zero", 4, "s1")  # CS low

    def xfer(byte, n=1):
        for _ in range(n):
            a.li("a0", byte)
            a.call("xfer")

    def div(v):
        a.li("t4", v)
        a.sw("t4", 0xC, "s1")
        a.lw("t2", 0xC, "s1")

    xfer(0x40)  # CMD0 at the reset divider
    xfer(0x00, 4)
    xfer(0x95)
    xfer(0xFF, 2)  # FF 01
    div(0)
    for b in (0x48, 0, 0, 1, 0xAA, 0x87):  # CMD8
        xfer(b)
    xfer(0xFF, 3)  # FF 01 00
    div(5)
    xfer(0xFF)  # 00
    div(255)
    xfer(0xFF)  # 01
    div(2)
    xfer(0xFF)  # AA
    a.sw("s2", 4, "s1")  # CS high
    xfer(0xFF)
    a.label("done")
    a.j("done")

    a.label("xfer")
    a.sw("a0", 0, "s1")
    a.lw("t0", 0, "s1")
    a.sw("s2", 0xC, "s1")  # ignored: busy
    a.lw("t2", 0xC, "s1")
    a.label("poll")
    a.lw("t1", 8, "s1")
    a.lw("t0", 0, "s1")
    a.bnez("t1", "poll")
    a.lw("a0", 0, "s1")
    a.ret()
    return a.assemble()


# --- firmware and demo programs --------------------------------------------------------
def _hex_words(path):
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


def hash_program(origin: int, soft_reset: bool = False) -> bytes:
    """Code that prints '#' (once the UART's TX FIFO has room: the first stage's last lines
    may still fill it) and stops, or soft-resets back to the first stage."""
    a = Asm(origin=origin)
    a.lui("a1", 0x40000)  # UART
    a.label("wait")
    a.lw("t0", 4, "a1")
    a.andi("t0", "t0", 2)  # TX full
    a.bne("t0", "zero", "wait")
    a.addi("a0", "zero", ord("#"))
    a.sb("a0", 0, "a1")
    if soft_reset:
        a.lui("a2", 0x40003)  # GPIO
        a.lui("a3", 0xA)
        a.addi("a3", "a3", 0x55A)  # SOFT_RESET_KEY's low half (0x0000_A55A)
        a.sw("a3", 0xC, "a2")
    a.label("stop")
    a.jal("zero", "stop")
    words = a.assemble()
    return struct.pack(f"<{len(words)}I", *words)


def _vux_tool():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import tools.vux_tool as vux_tool

    return vux_tool


def _card_sectors(files: dict) -> dict:
    """A FAT32 card holding files ({"VUX9K/STAGE2.BIN": bytes}) as {lba: sector}."""
    img = _vux_tool().build_fat32_image(files)
    sectors = {}
    for lba in range(len(img) // 512):
        sector = img[lba * 512 : (lba + 1) * 512]
        if any(sector):
            sectors[lba] = sector
    return sectors


def stage2_card() -> dict:
    """A FAT32 card with \\VUX9K\\STAGE2.BIN printing '#', as {lba: sector}."""
    data, _ = _vux_tool().build_vux9_v4_image(hash_program(STAGE2_BASE), name="test", load_addr=STAGE2_BASE)
    return _card_sectors({"VUX9K/STAGE2.BIN": data})


# The real second stage and the hello demo (make firmware hello), for s2_run_hello
STAGE2_FILE = os.path.join(REPO_ROOT, "build", "firmware", "stage2", "STAGE2.BIN")
HELLO_FILE = os.path.join(REPO_ROOT, "build", "firmware", "apps", "HELLO.BIN")
# The second stage's prompt comes at cycle ~14.44M; the hello demo's last line (its
# a0-a2 and the key it waits for) has left the UART by ~20.26M
S2_RUN_HELLO_AT = 14_600_000
S2_RUN_HELLO_CYCLES = 20_300_000


def s2_hello_card() -> dict:
    """A FAT32 card with the real \\VUX9K\\STAGE2.BIN and \\VUX9K\\APPS\\HELLO.BIN, as
    {lba: sector}; empty when they haven't been built (test_soc_lockstep fails on that
    when it runs the program)."""
    files = {}
    for dest, path in (("VUX9K/STAGE2.BIN", STAGE2_FILE), ("VUX9K/APPS/HELLO.BIN", HELLO_FILE)):
        if not os.path.exists(path):
            return {}
        with open(path, "rb") as f:
            files[dest] = f.read()
    return _card_sectors(files)


def programs() -> list:
    """All programs, in trace-id order (id = index + 1)."""
    progs = [Program(f"random_rv_{seed}", cycles=20_000, imem_words=random_rv(seed)) for seed in (1, 2, 3)]
    progs.append(Program("traps", cycles=6_000, imem_words=traps(), uart_rx=[(TRAPS_RX_CYCLE, b"Z")]))
    hack_hex = os.path.join(REPO_ROOT, "build", "hack", "firmware.hex")
    progs.append(Program("hack_demo", cycles=1_250_000, imem_words=_hex_words(hack_hex)))
    # The first stage: from power-on it boots STAGE2.BIN, which runs from the PSRAM ('#')
    progs.append(Program("s1_autoboot", cycles=3_300_000, sd_sectors=stage2_card()))
    # A key right after the banner keeps it at its prompt; then h and i
    progs.append(
        Program(
            "s1_cli",
            cycles=3_600_000,
            sd_sectors=stage2_card(),
            uart_rx=[(600_000, b"\r"), (1_300_000, b"h"), (2_000_000, b"i")],
            slow=True,
        )
    )
    progs.append(Program("hack_soft_reset", cycles=20_000, imem_words=hack_soft_reset()))
    progs.append(Program("boot_mode_hack", cycles=6_000, imem_words=boot_mode_hack()))
    progs.append(Program("sd_clkdiv", cycles=30_000, imem_words=sd_clkdiv()))
    progs.append(Program("s2_launch", cycles=S2_LAUNCH_CYCLES, imem_words=s2_launch()))
    progs.append(Program("s2_launch_hack", cycles=S2_LAUNCH_CYCLES, imem_words=s2_launch(hack=True)))
    # The window programs finish by cycle ~3,600 / ~4,000 (window waits included); xmem
    # then takes timer interrupts in I-RAM until the end
    progs.append(Program("xmem", cycles=6_500, imem_words=xmem()))
    progs.append(Program("random_rv_xmem_4", cycles=8_000, imem_words=random_rv(4, base=XMEM + 0x400)))
    # The instruction cache: done by cycle ~3,800, then spins in I-RAM
    progs.append(Program("xcache", cycles=5_000, imem_words=xcache()))
    # The whole chain with the real second stage: the first stage boots it from the card,
    # its `run` starts the hello demo in the PSRAM, which prints a0-a2
    progs.append(
        Program(
            "s2_run_hello",
            cycles=S2_RUN_HELLO_CYCLES,
            sd_sectors=s2_hello_card(),
            uart_rx=[(S2_RUN_HELLO_AT, b"run VUX9K/APPS/HELLO.BIN\r")],
            slow=True,
            needs=[STAGE2_FILE, HELLO_FILE],
        )
    )
    seeds = os.environ.get("LOCKSTEP_SEEDS", "")
    for s in [int(x) for x in seeds.split(",") if x]:
        progs.append(Program(f"random_rv_{s}", cycles=200_000, imem_words=random_rv(s, blocks=1500), slow=True))
    return progs
