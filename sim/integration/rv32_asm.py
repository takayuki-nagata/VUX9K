# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Minimal RV32I (+ Zicsr) assembler for small SoC test programs (sim/integration/rv32_asm.py).

Only what the SoC tests need: base integer instructions, CSR access, mret, a few
pseudo-instructions and forward/backward labels. Programs are built in Python,
e.g.
    a = Asm()
    a.label("loop"); a.addi("t0", "t0", 1); a.j("loop")
    words = a.assemble()
Encodings are checked against GNU as output by selftest().
"""

REGS = {f"x{i}": i for i in range(32)}
REGS.update(
    {
        n: i
        for i, n in enumerate(
            "zero ra sp gp tp t0 t1 t2 s0 s1 a0 a1 a2 a3 a4 a5 a6 a7 "
            "s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 t3 t4 t5 t6".split()
        )
    }
)
REGS["fp"] = 8

CSRS = {"mstatus": 0x300, "mie": 0x304, "mtvec": 0x305, "mscratch": 0x340, "mepc": 0x341, "mcause": 0x342, "mip": 0x344}


def _r(name):
    return REGS[name]


def _signed_fits(value, bits):
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def r_type(funct7, rs2, rs1, funct3, rd, opcode=0x33):
    return (funct7 << 25) | (_r(rs2) << 20) | (_r(rs1) << 15) | (funct3 << 12) | (_r(rd) << 7) | opcode


def i_type(imm, rs1, funct3, rd, opcode):
    assert _signed_fits(imm, 12), f"I-immediate out of range: {imm}"
    return ((imm & 0xFFF) << 20) | (_r(rs1) << 15) | (funct3 << 12) | (_r(rd) << 7) | opcode


def s_type(imm, rs2, rs1, funct3):
    assert _signed_fits(imm, 12), f"S-immediate out of range: {imm}"
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (_r(rs2) << 20) | (_r(rs1) << 15) | (funct3 << 12) | ((imm & 0x1F) << 7) | 0x23


def b_type(offset, rs2, rs1, funct3):
    assert offset % 2 == 0 and _signed_fits(offset, 13), f"branch offset out of range: {offset}"
    o = offset & 0x1FFF
    return (
        (((o >> 12) & 1) << 31)
        | (((o >> 5) & 0x3F) << 25)
        | (_r(rs2) << 20)
        | (_r(rs1) << 15)
        | (funct3 << 12)
        | (((o >> 1) & 0xF) << 8)
        | (((o >> 11) & 1) << 7)
        | 0x63
    )


def u_type(imm20, rd, opcode):
    return ((imm20 & 0xFFFFF) << 12) | (_r(rd) << 7) | opcode


def j_type(offset, rd):
    assert offset % 2 == 0 and _signed_fits(offset, 21), f"jump offset out of range: {offset}"
    o = offset & 0x1FFFFF
    return (
        (((o >> 20) & 1) << 31)
        | (((o >> 1) & 0x3FF) << 21)
        | (((o >> 11) & 1) << 20)
        | (((o >> 12) & 0xFF) << 12)
        | (_r(rd) << 7)
        | 0x6F
    )


class Asm:
    """Two-pass assembler: instructions may reference labels defined later."""

    def __init__(self, origin=0):
        self.origin = origin
        self._items = []  # int word, or callable(pc, labels) -> int
        self.labels = {}

    @property
    def pc(self):
        return self.origin + 4 * len(self._items)

    def label(self, name):
        assert name not in self.labels, f"duplicate label {name}"
        self.labels[name] = self.pc

    def word(self, value):
        self._items.append(value & 0xFFFFFFFF)

    def _emit(self, item):
        self._items.append(item)

    def assemble(self):
        words = []
        for i, item in enumerate(self._items):
            pc = self.origin + 4 * i
            words.append(item(pc, self.labels) if callable(item) else item)
        return words

    # --- base integer ---------------------------------------------------------
    def lui(self, rd, imm20):
        self._emit(u_type(imm20, rd, 0x37))

    def auipc(self, rd, imm20):
        self._emit(u_type(imm20, rd, 0x17))

    def addi(self, rd, rs1, imm):
        self._emit(i_type(imm, rs1, 0, rd, 0x13))

    def andi(self, rd, rs1, imm):
        self._emit(i_type(imm, rs1, 7, rd, 0x13))

    def ori(self, rd, rs1, imm):
        self._emit(i_type(imm, rs1, 6, rd, 0x13))

    def slli(self, rd, rs1, shamt):
        self._emit(i_type(shamt, rs1, 1, rd, 0x13))

    def add(self, rd, rs1, rs2):
        self._emit(r_type(0, rs2, rs1, 0, rd))

    def sub(self, rd, rs1, rs2):
        self._emit(r_type(0x20, rs2, rs1, 0, rd))

    def lw(self, rd, imm, rs1):
        self._emit(i_type(imm, rs1, 2, rd, 0x03))

    def lb(self, rd, imm, rs1):
        self._emit(i_type(imm, rs1, 0, rd, 0x03))

    def lbu(self, rd, imm, rs1):
        self._emit(i_type(imm, rs1, 4, rd, 0x03))

    def lh(self, rd, imm, rs1):
        self._emit(i_type(imm, rs1, 1, rd, 0x03))

    def sw(self, rs2, imm, rs1):
        self._emit(s_type(imm, rs2, rs1, 2))

    def sh(self, rs2, imm, rs1):
        self._emit(s_type(imm, rs2, rs1, 1))

    def sb(self, rs2, imm, rs1):
        self._emit(s_type(imm, rs2, rs1, 0))

    def _branch(self, funct3, rs1, rs2, target):
        self._emit(lambda pc, labels: b_type(labels[target] - pc, rs2, rs1, funct3))

    def beq(self, rs1, rs2, target):
        self._branch(0, rs1, rs2, target)

    def bne(self, rs1, rs2, target):
        self._branch(1, rs1, rs2, target)

    def bltu(self, rs1, rs2, target):
        self._branch(6, rs1, rs2, target)

    def jal(self, rd, target):
        self._emit(lambda pc, labels: j_type(labels[target] - pc, rd))

    def jalr(self, rd, rs1, imm=0):
        self._emit(i_type(imm, rs1, 0, rd, 0x67))

    # --- Zicsr / privileged ---------------------------------------------------
    def csrrw(self, rd, csr, rs1):
        self._emit(i_type(0, rs1, 1, rd, 0x73) | (CSRS[csr] << 20))

    def csrrs(self, rd, csr, rs1):
        self._emit(i_type(0, rs1, 2, rd, 0x73) | (CSRS[csr] << 20))

    def mret(self):
        self._emit(0x30200073)

    # --- pseudo-instructions ----------------------------------------------------
    def li(self, rd, value):
        value &= 0xFFFFFFFF
        signed = value - (1 << 32) if value & 0x80000000 else value
        if _signed_fits(signed, 12):
            self.addi(rd, "zero", signed)
            return
        lo = value & 0xFFF
        lo = lo - 0x1000 if lo & 0x800 else lo
        self.lui(rd, ((value - lo) >> 12) & 0xFFFFF)
        if lo:
            self.addi(rd, rd, lo)

    def la(self, rd, target):
        """Absolute address of a label (programs here run at a fixed origin)."""
        self._emit(lambda pc, labels: u_type(_hi20(labels[target]), rd, 0x37))
        self._emit(lambda pc, labels: i_type(_lo12(labels[target]), rd, 0, rd, 0x13))

    def mv(self, rd, rs):
        self.addi(rd, rs, 0)

    def nop(self):
        self.addi("zero", "zero", 0)

    def j(self, target):
        self.jal("zero", target)

    def call(self, target):
        self.jal("ra", target)

    def ret(self):
        self.jalr("zero", "ra", 0)

    def beqz(self, rs, target):
        self.beq(rs, "zero", target)

    def bnez(self, rs, target):
        self.bne(rs, "zero", target)

    def csrw(self, csr, rs):
        self.csrrw("zero", csr, rs)

    def csrr(self, rd, csr):
        self.csrrs(rd, csr, "zero")

    def csrs(self, csr, rs):
        self.csrrs("zero", csr, rs)


def _lo12(value):
    lo = value & 0xFFF
    return lo - 0x1000 if lo & 0x800 else lo


def _hi20(value):
    return ((value - _lo12(value)) >> 12) & 0xFFFFF


def selftest():
    """Compare against encodings produced by GNU as (riscv64-zephyr-elf-as -march=rv32i_zicsr)."""
    a = Asm()
    a.label("top")
    a.lui("a1", 0x40000)  # 400005b7
    a.addi("a0", "zero", 0x23)  # 02300513
    a.sb("a0", 0, "a1")  # 00a58023
    a.jal("zero", "top0")  # 0000006f (j .)
    a.label("top0")
    words = a.assemble()
    words[3] = j_type(0, "zero")
    expected = [0x400005B7, 0x02300513, 0x00A58023, 0x0000006F]
    assert words == expected, [hex(w) for w in words]
    checks = {
        "sw t0,-4(s2)": (s_type(-4, "t0", "s2", 2), 0xFE592E23),
        "lw t3,8(s4)": (i_type(8, "s4", 2, "t3", 0x03), 0x008A2E03),
        "lb t3,1(s2)": (i_type(1, "s2", 0, "t3", 0x03), 0x00190E03),
        "bne t3,t2,+64": (b_type(64, "t2", "t3", 1), 0x047E1063),
        "beq t0,t1,-8": (b_type(-8, "t1", "t0", 0), 0xFE628CE3),
        "jal ra,+2048": (j_type(2048, "ra"), 0x001000EF),
        "csrrw zero,mtvec,t0": (i_type(0, "t0", 1, "zero", 0x73) | (0x305 << 20), 0x30529073),
        "csrrs t0,mcause,zero": (i_type(0, "zero", 2, "t0", 0x73) | (0x342 << 20), 0x342022F3),
        "sub t0,t1,t2": (r_type(0x20, "t2", "t1", 0, "t0"), 0x407302B3),
    }
    for text, (got, want) in checks.items():
        assert got == want, f"{text}: got 0x{got:08x}, want 0x{want:08x}"
