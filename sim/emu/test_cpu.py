# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""CPU basics on the emulator, through programs (ported from sim/emulator/test_emulator.py)."""

import pytest
from lockstep_programs import hack_soft_reset
from rv32_asm import Asm
from vux9k import Soc


def org(a, addr):
    """Pad with NOPs up to addr."""
    while a.pc < addr:
        a.nop()


def run_program(build, steps):
    a = Asm()
    build(a)
    soc = Soc("real")
    soc.load_iram_words(a.assemble())
    for _ in range(steps):
        soc.step()
    return soc


def test_uart_send_at_a_past_cycle_is_refused():
    soc = run_program(lambda a: [a.nop() for _ in range(4)], 4)
    with pytest.raises(ValueError, match="before the current cycle"):
        soc.uart_send(b"x", at=soc.cycle - 1)
    soc.uart_send(b"x", at=soc.cycle)
    soc.uart_send(b"y")


def test_csr_write_and_read_back():
    def prog(a):
        a.li("t0", 0x8000)
        a.csrw("mtvec", "t0")
        a.csrr("a0", "mtvec")
        a.li("t0", 0x8)
        a.csrw("mstatus", "t0")
        a.csrr("a1", "mstatus")
        a.li("t0", 0x80)
        a.csrw("mie", "t0")
        a.csrr("a2", "mie")

    soc = run_program(prog, 9)
    assert soc.regs[10] == 0x8000
    assert soc.regs[11] == 0x1808, "MIE set, MPP reads as M"
    assert soc.regs[12] == 0x80


def test_ecall_traps_and_mret_returns():
    def prog(a):
        a.li("t0", 0x100)
        a.csrw("mtvec", "t0")
        a.li("t0", 0x8)
        a.csrw("mstatus", "t0")
        a.label("call")
        a.ecall()
        a.label("spin")
        a.j("spin")
        org(a, 0x100)
        a.csrr("t1", "mepc")
        a.addi("t1", "t1", 4)
        a.csrw("mepc", "t1")
        a.mret()

    soc = run_program(prog, 5)
    assert soc.pc == 0x100
    assert (soc.mepc, soc.mcause) == (0x10, 11)
    assert soc.mstatus & 0x88 == 0x80, "MIE cleared, MPIE set"
    for _ in range(4):
        soc.step()
    assert soc.pc == 0x14
    assert soc.mstatus & 0x8, "MIE restored"


def test_timer_interrupt():
    def prog(a):
        a.li("t0", 0x200)
        a.csrw("mtvec", "t0")
        a.li("t1", 0x40001000)
        a.sw("zero", 0xC, "t1")
        a.li("t0", 150)
        a.sw("t0", 0x8, "t1")
        a.li("t0", 0x80)
        a.csrw("mie", "t0")
        a.li("t0", 0x8)
        a.csrw("mstatus", "t0")
        a.label("spin")
        a.j("spin")
        org(a, 0x200)
        a.label("handler")
        a.j("handler")

    soc = run_program(prog, 0)
    soc.run(400)
    assert soc.pc == 0x200
    assert soc.mcause == 0x8000_0007
    assert soc.mstatus & 0x88 == 0x80


def test_byte_and_halfword_stores_to_dram():
    def prog(a):
        a.li("x1", 0x20000000)
        a.li("x3", -1)
        a.sw("x3", 0, "x1")
        a.li("x2", 0xAB)
        a.sb("x2", 1, "x1")
        a.li("x2", 0x1234)
        a.sh("x2", 2, "x1")

    soc = run_program(prog, 8)
    assert soc.dram_word(0) == 0x1234ABFF


@pytest.mark.parametrize("key_by_addition", [True, False])
def test_hack_soft_reset_key(key_by_addition):
    """A Hack program can soft-reset the CPU (lockstep_programs.hack_soft_reset): the
    key it forms by addition is accepted, the one formed with ! is not."""
    soc = Soc("real")
    soc.load_iram_words(hack_soft_reset(key_by_addition))
    soc.run(200_000)  # ~17 UART bytes' time
    assert not soc.riscv_mode
    sent = soc.uart_received()
    if key_by_addition:
        assert sent.count(b"R") >= 2, sent  # restarted from 0 after the soft reset
    else:
        assert sent == b"R", sent
