# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Minimal Hack assembler for small SoC test programs (sim/integration/hack_asm.py).

Standard nand2tetris encoding: `@value` / `@label` A-instructions and
`dest=comp;jump` C-instructions, plus labels. Programs are built in Python, e.g.
    a = Asm()
    a.label("loop"); a.at("loop"); a.c("0;JMP")
    words = a.assemble()
assemble() packs two instructions per 32-bit I-RAM word the way
scripts/bin2hex.py does for hack_demo (first instruction in the low half).
Encodings are checked against the nand2tetris reference by selftest().
"""

COMP = {
    "0": 0b0101010,
    "1": 0b0111111,
    "-1": 0b0111010,
    "D": 0b0001100,
    "A": 0b0110000,
    "!D": 0b0001101,
    "!A": 0b0110001,
    "-D": 0b0001111,
    "-A": 0b0110011,
    "D+1": 0b0011111,
    "A+1": 0b0110111,
    "D-1": 0b0001110,
    "A-1": 0b0110010,
    "D+A": 0b0000010,
    "D-A": 0b0010011,
    "A-D": 0b0000111,
    "D&A": 0b0000000,
    "D|A": 0b0010101,
}
# M-forms: same ALU op with the a-bit set (A -> M)
COMP.update({k.replace("A", "M"): v | 0b1000000 for k, v in list(COMP.items()) if "A" in k})

DEST_BITS = {"A": 0b100, "D": 0b010, "M": 0b001}
JUMP = {"": 0, "JGT": 1, "JEQ": 2, "JGE": 3, "JLT": 4, "JNE": 5, "JLE": 6, "JMP": 7}


def encode_c(text: str) -> int:
    """Encode `dest=comp;jump` (dest and jump optional)."""
    dest, _, rest = text.rpartition("=")
    comp, _, jump = rest.partition(";")
    d = 0
    for ch in dest:
        d |= DEST_BITS[ch]
    return 0xE000 | (COMP[comp.strip()] << 6) | (d << 3) | JUMP[jump.strip()]


class Asm:
    def __init__(self):
        self.items = []  # int (encoded) or str (label reference for @label)
        self.labels = {}

    def label(self, name: str):
        assert name not in self.labels, f"duplicate label {name}"
        self.labels[name] = len(self.items)

    def at(self, value):
        """`@value` (0..0x7FFF) or `@label`."""
        if isinstance(value, str):
            self.items.append(value)
        else:
            assert 0 <= value <= 0x7FFF, f"A-instruction value out of range: {value}"
            self.items.append(value)

    def c(self, text: str):
        self.items.append(encode_c(text))

    def instructions(self) -> list[int]:
        return [self.labels[i] if isinstance(i, str) else i for i in self.items]

    def assemble(self) -> list[int]:
        instrs = self.instructions()
        if len(instrs) % 2:
            instrs.append(0)
        return [(instrs[i + 1] << 16) | instrs[i] for i in range(0, len(instrs), 2)]


def selftest():
    # Reference encodings from the nand2tetris Hack assembler
    cases = {
        "D=M": 0xFC10,
        "M=D": 0xE308,
        "0;JMP": 0xEA87,
        "D;JGT": 0xE301,
        "D;JNE": 0xE305,
        "D=D+A": 0xE090,
        "AM=M-1": 0xFCA8,
        "D=D&A": 0xE010,
        "MD=M+1": 0xFDD8,
        "AMD=D|M": 0xF578,
        "D=A-D": 0xE1D0,
    }
    for text, want in cases.items():
        got = encode_c(text)
        assert got == want, f"{text}: got 0x{got:04X}, want 0x{want:04X}"
    a = Asm()
    a.at("end")
    a.c("0;JMP")
    a.label("end")
    a.at(0x6000)
    assert a.assemble() == [0xEA87_0002, 0x0000_6000], [hex(w) for w in a.assemble()]
