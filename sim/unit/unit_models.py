# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Shared by the unit tests (sim/unit/): operand generators (boundary values and random
values biased toward them) and reference models used by more than one test.

Randomness comes from Python's `random`, which cocotb seeds per test from
COCOTB_RANDOM_SEED (fixed by sim/runners/sim_runner.py unless the environment sets it;
the seed is in the test log). Rerun a failure with that COCOTB_RANDOM_SEED.
"""

import random

# 32-bit values where carries, signs and shifts change behavior
EDGES32 = (
    0x0000_0000,
    0x0000_0001,
    0x0000_0002,
    0x0000_001F,
    0x0000_0020,
    0x7FFF_FFFE,
    0x7FFF_FFFF,
    0x8000_0000,
    0x8000_0001,
    0xFFFF_FFFE,
    0xFFFF_FFFF,
    0x5555_5555,
    0xAAAA_AAAA,
)


def rand32():
    """A 32-bit value: a boundary value, a small or near-boundary one, or uniform."""
    r = random.random()
    if r < 0.2:
        return random.choice(EDGES32)
    if r < 0.35:
        return (random.choice(EDGES32) + random.randint(-3, 3)) & 0xFFFF_FFFF
    if r < 0.45:
        return random.getrandbits(random.randint(1, 8))
    return random.getrandbits(32)


def signed32(x):
    return x - (1 << 32) if x & 0x8000_0000 else x


def sext(value, bits):
    """Sign-extend a `bits`-wide value to 32 bits."""
    value &= (1 << bits) - 1
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value & 0xFFFF_FFFF


# rv32i_pkg::AluOp, in declaration order
(ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU, ALU_XOR,
 ALU_SRL, ALU_SRA, ALU_OR, ALU_AND, ALU_COPY_B, ALU_COPY_A) = range(12)  # fmt: skip


def alu_model(op, a, b):
    """rv32i_alu's result; op codes past COPY_A give 0."""
    sh = b & 0x1F
    return {
        ALU_ADD: (a + b) & 0xFFFF_FFFF,
        ALU_SUB: (a - b) & 0xFFFF_FFFF,
        ALU_SLL: (a << sh) & 0xFFFF_FFFF,
        ALU_SLT: int(signed32(a) < signed32(b)),
        ALU_SLTU: int(a < b),
        ALU_XOR: a ^ b,
        ALU_SRL: a >> sh,
        ALU_SRA: (signed32(a) >> sh) & 0xFFFF_FFFF,
        ALU_OR: a | b,
        ALU_AND: a & b,
        ALU_COPY_B: b,
        ALU_COPY_A: a,
    }.get(op, 0)


# The Hack comp table (c1..c6) as a function of D and Y (A, or M when a=1), plus the
# toolchain's D^Y extension (000101); callers take the low 16 bits
HACK_COMP = {
    0b101010: lambda d, y: 0,
    0b111111: lambda d, y: 1,
    0b111010: lambda d, y: -1,
    0b001100: lambda d, y: d,
    0b110000: lambda d, y: y,
    0b001101: lambda d, y: ~d,
    0b110001: lambda d, y: ~y,
    0b001111: lambda d, y: -d,
    0b110011: lambda d, y: -y,
    0b011111: lambda d, y: d + 1,
    0b110111: lambda d, y: y + 1,
    0b001110: lambda d, y: d - 1,
    0b110010: lambda d, y: y - 1,
    0b000010: lambda d, y: d + y,
    0b010011: lambda d, y: d - y,
    0b000111: lambda d, y: y - d,
    0b000000: lambda d, y: d & y,
    0b010101: lambda d, y: d | y,
    0b000101: lambda d, y: d ^ y,
}
