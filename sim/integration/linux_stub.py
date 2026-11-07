# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
A stand-in for an upstream riscv Linux `Image` and its device tree, for the second stage's
`linux` (sim/emu/test_stage2.py, test_soc_stage2.py): the 64-byte header the kernel's head.S
writes (code0 a jump over it, text_offset, image_size, magic "RISCV", magic2 "RSC\\x05"),
then RV32 code that prints what it was handed (a0-a2, mie, mstatus) and word sums of the
device tree at a1 and of its own file, read back through the caches, as one `[KERNEL]` line,
and spins. The device tree is bytes with dtc's header's first two words (magic, totalsize).
"""

import re
import struct

from rv32_asm import Asm

BASE = 0x8000_0000
DTB_ADDR = 0x807F_0000
FDT_MAGIC = 0xD00D_FEED
HEADER_LEN = 64
KERNEL_LINE = re.compile(
    r"\[KERNEL\] a0=([0-9A-F]{8}) a1=([0-9A-F]{8}) a2=([0-9A-F]{8}) mie=([0-9A-F]{8}) "
    r"mstatus=([0-9A-F]{8}) dtb=([0-9A-F]{8}) image=([0-9A-F]{8})\n"
)


def word_sum(data: bytes) -> int:
    """The sum of data's little-endian words, as the kernel stand-in adds them."""
    assert len(data) % 4 == 0
    return sum(struct.unpack(f"<{len(data) // 4}I", data)) & 0xFFFF_FFFF


def _pattern(n: int, seed: int) -> bytes:
    """n pseudo-random bytes."""
    out = bytearray()
    state = seed
    while len(out) < n:
        state = (state * 1103515245 + 12345) & 0xFFFF_FFFF
        out.append(state >> 24)
    return bytes(out)


def dtb(length: int = 1024, *, magic: int = FDT_MAGIC, totalsize: int | None = None) -> bytes:
    """A device tree of length bytes (a multiple of 4): magic and totalsize (its length
    unless given) big-endian, then pseudo-random bytes."""
    assert length % 4 == 0 and length >= 8
    size = length if totalsize is None else totalsize
    return struct.pack(">II", magic, size) + _pattern(length - 8, 0xD7B)


def image(dtb_len: int, *, pad: int = 4096, text_offset: int = 0, image_size: int | None = None,
          magic2: bytes = b"RSC\x05") -> bytes:  # fmt: skip
    """The kernel stand-in for a device tree of dtb_len bytes: header, code, pad bytes
    (pseudo-random; enough of them that loading the file evicts the device tree's lines from
    the 2 KB data cache). image_size is the file and 4 KB of bss unless given."""
    a = Asm(origin=BASE)
    a.jal("zero", "code")  # code0
    for _ in range(HEADER_LEN // 4 - 1):
        a.word(0)  # code1 and the fields, set below
    a.label("code")
    a.mv("s0", "a0")
    a.mv("s1", "a1")
    a.mv("s2", "a2")
    a.csrr("s3", "mie")
    a.csrr("s4", "mstatus")
    a.lui("s7", 0x40000)  # UART
    # The device tree's words from a1, and the file's from BASE
    a.mv("t0", "s1")
    a.li("t1", dtb_len // 4)
    a.li("s5", 0)
    a.label("dtb")
    a.lw("t2", 0, "t0")
    a.add("s5", "s5", "t2")
    a.addi("t0", "t0", 4)
    a.addi("t1", "t1", -1)
    a.bnez("t1", "dtb")
    a.li("t0", BASE)
    a.la("t1", "end")
    a.li("s6", 0)
    a.label("img")
    a.lw("t2", 0, "t0")
    a.add("s6", "s6", "t2")
    a.addi("t0", "t0", 4)
    a.bltu("t0", "t1", "img")
    # [KERNEL] a0=... a1=... a2=... mie=... mstatus=... dtb=... image=...
    for text, reg in [("[KERNEL] a0=", "s0"), (" a1=", "s1"), (" a2=", "s2"), (" mie=", "s3"),
                      (" mstatus=", "s4"), (" dtb=", "s5"), (" image=", "s6")]:  # fmt: skip
        for ch in text:
            a.li("a4", ord(ch))
            a.call("putc")
        a.mv("a3", reg)
        a.call("puthex")
    a.li("a4", ord("\n"))
    a.call("putc")
    a.label("spin")
    a.j("spin")
    # a3 in 8 hex digits (divu for the top nibble: the assembler has no srli)
    a.label("puthex")
    a.mv("t5", "ra")
    a.li("t3", 8)
    a.li("t6", 0x1000_0000)
    a.label("nibble")
    a.muldiv("divu", "a4", "a3", "t6")
    a.slli("a3", "a3", 4)
    a.li("t4", 10)
    a.bltu("a4", "t4", "digit")
    a.addi("a4", "a4", ord("A") - 10 - ord("0"))
    a.label("digit")
    a.addi("a4", "a4", ord("0"))
    a.call("putc")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "nibble")
    a.jalr("zero", "t5", 0)
    # a4 to the UART once its TX FIFO has room
    a.label("putc")
    a.lw("t4", 4, "s7")  # TXFULL
    a.bnez("t4", "putc")
    a.sb("a4", 0, "s7")
    a.ret()
    for w in struct.unpack(f"<{pad // 4}I", _pattern(pad // 4 * 4, 0x1234_5678)):
        a.word(w)
    a.label("end")
    data = bytearray(struct.pack(f"<{len(a.assemble())}I", *a.assemble()))
    size = len(data) + 4096 if image_size is None else image_size
    struct.pack_into("<QQ", data, 8, text_offset, size)
    struct.pack_into("<I", data, 0x2C, 2)  # version 0.2
    data[0x30:0x38] = b"RISCV\0\0\0"
    data[0x38:0x3C] = magic2
    return bytes(data)


def handoff(out: str) -> dict:
    """What the kernel stand-in printed: {"a0": ..., "dtb": ..., ...}."""
    m = KERNEL_LINE.search(out)
    assert m, out
    names = ("a0", "a1", "a2", "mie", "mstatus", "dtb", "image")
    return dict(zip(names, (int(g, 16) for g in m.groups()), strict=True))
