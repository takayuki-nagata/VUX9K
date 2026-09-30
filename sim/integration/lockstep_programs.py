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
I-RAM, SD sectors) plus host stimulus at exact cycles (cycle 0 = first cycle out of
reset): UART bytes sent back to back from a cycle, and S2 button levels.
"""

import os
import random
from dataclasses import dataclass, field

from rv32_asm import Asm, b_type, i_type, r_type

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UART_BIT = 156  # soc_pkg::UART_CNT: 18 MHz / 115200
UART_FRAME = 10 * UART_BIT


@dataclass
class Program:
    name: str
    cycles: int  # run length, from cycle 0
    imem_words: list = None  # over the firmware preload, from word 0
    uart_rx: list = field(default_factory=list)  # (cycle, bytes): start bits back to back from cycle
    button: list = field(default_factory=list)  # (cycle, pressed) S2 pin level from cycle
    sd_sectors: dict = field(default_factory=dict)  # lba -> bytes (after the MBR in sector 0)
    slow: bool = False  # only in the long run (make sim-lockstep-slow)


def _csr_read(rd, csr):
    return (csr << 20) | (2 << 12) | (rd << 7) | 0x73  # csrrs rd, csr, x0


# --- constrained-random RV32I ----------------------------------------------------------
# x2 (sp) = D-RAM base for loads/stores, x30 = timer base, x31 = loop counter; the rest
# are random. No traps: aligned accesses only, branches only forward or counted loops.
_R_OPS = [(0, 0), (0x20, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0x20, 5), (0, 6), (0, 7)]
_I_OPS = [0, 2, 3, 4, 6, 7]  # addi slti sltiu xori ori andi
_DEST = [f"x{i}" for i in range(1, 30) if i != 2]


def random_rv(seed: int, blocks: int = 300) -> list:
    rnd = random.Random(seed)
    a = Asm()
    a.li("sp", 0x20000800)
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
    assert len(words) <= 0x3800 // 4, f"random program of {len(words)} words overlaps the Resident Loader"
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


# --- firmware and demo programs --------------------------------------------------------
def _hex_words(path):
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


def _hash_slot1():
    """Slot 1 image of a program printing '#' (as test_soc_fast uses)."""
    import struct
    import sys

    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import tools.vux_tool as vux_tool

    payload = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)
    raw, meta = vux_tool.build_vux9_image(payload, slot=1, name="TestApp", mode="riscv")
    return {128 + i: raw[i * 512 : (i + 1) * 512] for i in range(meta["num_sectors"])}


def programs() -> list:
    """All programs, in trace-id order (id = index + 1)."""
    progs = [Program(f"random_rv_{seed}", cycles=20_000, imem_words=random_rv(seed)) for seed in (1, 2, 3)]
    progs.append(Program("traps", cycles=6_000, imem_words=traps(), uart_rx=[(TRAPS_RX_CYCLE, b"Z")]))
    hack_hex = os.path.join(REPO_ROOT, "build", "hack", "firmware.hex")
    progs.append(Program("hack_demo", cycles=1_250_000, imem_words=_hex_words(hack_hex)))
    progs.append(
        Program(
            "boot_cli",
            cycles=3_000_000,
            uart_rx=[(2_300_000, b"h"), (2_400_000, b"s")],
        )
    )
    progs.append(
        Program(
            "boot_s2",
            cycles=4_000_000,
            sd_sectors=_hash_slot1(),
            button=[(2_300_000, True), (2_300_000 + 675_000, False)],
            slow=True,
        )
    )
    seeds = os.environ.get("LOCKSTEP_SEEDS", "")
    for s in [int(x) for x in seeds.split(",") if x]:
        progs.append(Program(f"random_rv_{s}", cycles=200_000, imem_words=random_rv(s, blocks=1500), slow=True))
    return progs
