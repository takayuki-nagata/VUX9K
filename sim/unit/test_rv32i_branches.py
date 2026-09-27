# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu: every RV32I conditional branch over operands of all sign combinations
(and x0), checked by the program itself (it reaches "ok", or "bad" on the first
wrong decision). Runs on the RTL and on the synthesized netlist (GLS): the netlist
once compared BLT/BGE unsigned, because Yosys reads the `int'()` casts Veryl emits
for `as i32` as a plain resize, while Icarus/Verilator treat them as signed.
"""

import cocotb
from rv32_asm import Asm, b_type
from test_unified_cpu_traps import run_program

VALUES = [0, 1, -1, -2, 0x7FFFFFFF, -0x80000000, 0x12345678, -0x12345678]
BRANCHES = {
    "beq": (0, lambda x, y: x == y),
    "bne": (1, lambda x, y: x != y),
    "blt": (4, lambda x, y: x < y),
    "bge": (5, lambda x, y: x >= y),
    "bltu": (6, lambda x, y: (x & 0xFFFFFFFF) < (y & 0xFFFFFFFF)),
    "bgeu": (7, lambda x, y: (x & 0xFFFFFFFF) >= (y & 0xFFFFFFFF)),
}


def branch_program(names):
    a = Asm()
    n = 0
    for x in VALUES:
        for y in VALUES + [None]:  # None: rs2 = x0
            for name in names:
                f3, taken = BRANCHES[name]
                a.li("s1", x & 0xFFFFFFFF)
                rs2 = "zero" if y is None else "s3"
                if y is not None:
                    a.li("s3", y & 0xFFFFFFFF)
                target = f"t{n}"
                a._emit(lambda pc, labels, f3=f3, rs2=rs2, target=target: b_type(labels[target] - pc, rs2, "s1", f3))
                expect = taken(x, 0 if y is None else y)
                a.j("bad" if expect else f"n{n}")  # fall-through
                a.label(target)
                if not expect:
                    a.j("bad")
                a.label(f"n{n}")
                n += 1
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels, n


async def check(dut, names):
    words, labels, n = branch_program(names)
    pcs, _ = await run_program(dut, words, max_cycles=12 * n + 200)
    if labels["bad"] in pcs:
        # the case is the last n-label passed before reaching "bad"
        passed = [k for k in range(n) if labels[f"n{k}"] in pcs]
        raise AssertionError(f"wrong branch decision after {len(passed)} of {n} cases")
    assert labels["ok"] in pcs, f"never reached 'ok'; last PCs {[hex(p) for p in pcs[-4:]]}"


@cocotb.test()
async def test_signed_branches(dut):
    """BLT/BGE compare as signed for every sign combination, including rs2 = x0"""
    await check(dut, ["blt", "bge"])


@cocotb.test()
async def test_unsigned_and_equality_branches(dut):
    """BLTU/BGEU compare as unsigned, BEQ/BNE on equality"""
    await check(dut, ["bltu", "bgeu", "beq", "bne"])
