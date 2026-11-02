# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""rv32a_unit: funct5's decode, the reservation's rules and an AMO's new value against a model
written from the A extension's spec, with rv32i_alu's results computed here."""

import random
from collections.abc import Callable

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer
from unit_models import EDGES32, alu_model, rand32

MASK = 0xFFFF_FFFF
LR, SC = 0b00010, 0b00011
OPS: dict[int, tuple[str, Callable[[int, int], int]]] = {
    0b00000: ("ADD", lambda o, s: (o + s) & MASK),
    0b00001: ("COPY_B", lambda o, s: s),
    0b00100: ("XOR", lambda o, s: o ^ s),
    0b01000: ("OR", lambda o, s: o | s),
    0b01100: ("AND", lambda o, s: o & s),
    0b10000: ("SLT", lambda o, s: min(o, s, key=_signed)),
    0b10100: ("SLT", lambda o, s: max(o, s, key=_signed)),
    0b11000: ("SLTU", min),
    0b11100: ("SLTU", max),
}
# rv32i_pkg::AluOp's members, in order
ALU_OPS = ("ADD", "SUB", "SLL", "SLT", "SLTU", "XOR", "SRL", "SRA", "OR", "AND", "COPY_B", "COPY_A")


def _signed(v):
    return v - (1 << 32) if v >> 31 else v


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for name in ("soft_rst", "funct5", "addr", "lr_exec", "sc_exec", "trap", "old_value", "rs2", "alu_out"):
        getattr(dut, name).value = 0
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)


@fcov.point("amo.funct5", tuple(OPS), xf=lambda f5, o, s: f5)
@fcov.point("amo.order", ("lt", "eq", "gt"), xf=lambda f5, o, s: "lt" if o < s else "eq" if o == s else "gt")
def sample_op(f5, o, s):
    pass


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_new_value(dut):
    """Every AMO on edge values: the ALU operation it asks for, and the word it stores"""
    await reset(dut)
    pairs = [(a, b) for a in EDGES32 for b in EDGES32] + [(rand32(), rand32()) for _ in range(200)]
    for f5, (alu, model) in OPS.items():
        for old, rs2 in pairs:
            sample_op(f5, old, rs2)
            dut.funct5.value = f5
            await Timer(1, unit="ns")
            op = int(dut.alu_op.value)
            assert ALU_OPS[op] == alu, f"funct5 {f5:05b}: ALU operation {ALU_OPS[op]}"
            dut.old_value.value, dut.rs2.value = old, rs2
            dut.alu_out.value = alu_model(op, old, rs2) & MASK
            await Timer(1, unit="ns")
            got = int(dut.new_value.value)
            assert got == model(old, rs2), f"funct5 {f5:05b} old {old:#x} rs2 {rs2:#x}: {got:#x}"
            assert not int(dut.is_lr.value) and not int(dut.is_sc.value)
    for f5, lr, sc in ((LR, 1, 0), (SC, 0, 1)):
        dut.funct5.value = f5
        await Timer(1, unit="ns")
        assert (int(dut.is_lr.value), int(dut.is_sc.value)) == (lr, sc)
    fcov.export()


@fcov.point("amo.resv", ("lr", "sc_hit", "sc_miss_addr", "sc_miss_none", "trap", "lr_again", "idle"), xf=lambda e: e)
def sample_event(event):
    pass


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_reservation_random(dut):
    """Random LRs, SCs, traps and idle cycles against the rules: an LR reserves its word, any SC
    or trap clears, a trap wins over an LR in its cycle; soft reset clears too"""
    await reset(dut)
    resv = None
    addrs = [0x8000_0000, 0x8000_0004, 0x8000_0008, 0x0000_0000, 0x7FFF_FFFC, 0xFFFF_FFFC]
    for i in range(4000):
        addr = random.choice(addrs)
        dut.addr.value = addr >> 2
        await Timer(1, unit="ns")
        assert int(dut.sc_ok.value) == int(resv == addr), f"cycle {i}: sc_ok at {addr:#x}, reserved {resv}"
        kind = random.choice(("lr", "sc", "trap", "both", "idle", "soft"))
        if kind == "sc":
            event = "sc_hit" if resv == addr else "sc_miss_addr" if resv is not None else "sc_miss_none"
        elif kind == "lr":
            event = "lr_again" if resv is not None else "lr"
        else:
            event = "trap" if kind in ("trap", "both") else "idle"
        sample_event(event)
        dut.lr_exec.value = int(kind in ("lr", "both"))
        dut.sc_exec.value = int(kind == "sc")
        dut.trap.value = int(kind in ("trap", "both"))
        dut.soft_rst.value = int(kind == "soft")
        await FallingEdge(dut.clk)
        if kind == "lr":
            resv = addr
        elif kind != "idle":
            resv = None
        dut.lr_exec.value = dut.sc_exec.value = dut.trap.value = dut.soft_rst.value = 0
    fcov.export()
