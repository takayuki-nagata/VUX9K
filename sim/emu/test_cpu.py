# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""CPU basics on the emulator, through programs (ported from sim/emulator/test_emulator.py)."""

import pytest
from vux9k import HACK_BACK, HACK_D_BASE, HACK_I_BASE, XMEM_BASE, Soc, start_hack  # first: sets sys.path

from lockstep_programs import boot_mode_hack, hack_soft_reset  # isort: skip
from rv32_asm import Asm  # isort: skip


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
    key it forms by addition is accepted, and with no mode given the CPU restarts in RV32
    (hack_stub prints HACK_BACK); the key formed with ! is not."""
    soc = start_hack(hack_soft_reset(key_by_addition))
    soc.run(200_000)  # ~17 UART bytes' time
    sent = soc.uart_received()
    if key_by_addition:
        assert sent == b"R" + bytes([HACK_BACK]), sent
        assert soc.riscv_mode
    else:
        assert sent == b"R", sent
        assert not soc.riscv_mode


def test_boot_mode_starts_hack_at_0():
    """The mode in GPIO 0x8 decides the ISA after a soft reset (lockstep_programs.boot_mode_hack):
    a Hack program starting with `@0`, written into its I segment, runs as Hack and prints 'H'."""
    soc = Soc("real")
    soc.load_iram_words(boot_mode_hack())
    soc.run(5_000)
    assert not soc.riscv_mode
    assert soc.uart_received() == b"H"


def test_power_on_starts_rv32():
    """No ISA guess: whatever I-RAM holds runs as RV32 after the power-on reset (a Hack-looking
    `@7` word is an illegal instruction)."""
    soc = Soc("real")
    soc.load_iram_words([0x0007_0007])
    soc.step()
    assert soc.riscv_mode
    assert soc.mcause == 2


def test_hack_runs_from_its_segments():
    """Hack fetches from its I segment and keeps data word A at HACK_D_BASE + 4 * A, a 32-bit
    word each, all through the window (no D-RAM, no 2K alias)."""
    import hack_asm

    h = hack_asm.Asm()
    h.at(0x1234)
    h.c("D=A")
    h.at(0x0805)
    h.c("M=D")
    h.at(5)
    h.c("D=M")  # word 5 is not word 0x805
    h.label("halt")
    h.at("halt")
    h.c("0;JMP")
    soc = Soc("real")
    soc.load_app(b"".join(i.to_bytes(2, "big") for i in h.instructions()), "hack")
    assert soc.xmem_word(HACK_I_BASE) == h.assemble()[0]
    for _ in range(6):
        soc.step()
    assert soc.xmem_word(HACK_D_BASE + 4 * 0x805) == 0x1234
    assert soc.regs[2] == 0
    assert soc.dram_word(5) == 0 and soc.dram_word(0x805) == 0


def test_load_app_into_the_psram():
    a = Asm(XMEM_BASE)
    a.auipc("a0", 0)
    a.lw("a1", 0, "a0")
    words = a.assemble()
    image = b"".join(w.to_bytes(4, "little") for w in words)
    soc = Soc("real")
    soc.load_app(image, "riscv", XMEM_BASE)
    assert (soc.pc, soc.iram_word(0)) == (XMEM_BASE, 0)
    assert [soc.xmem_word(XMEM_BASE + 4 * i) for i in range(2)] == words
    soc.step()
    soc.step()
    assert soc.regs[10:12] == [XMEM_BASE, words[0]]
    soc.load_xmem(XMEM_BASE + 8, b"\x01\x02\x03")
    assert soc.xmem_word(XMEM_BASE + 8) == 0x030201
    # A Hack image's load address is an offset in its segment
    with pytest.raises(ValueError, match="Hack instruction segment"):
        soc.load_app(image, "hack", XMEM_BASE)
    with pytest.raises(ValueError, match="word-aligned"):
        soc.load_xmem(0x2000_0000, image)
    # No window in the riscv-tests profile
    assert Soc("isa-test").xmem_word(XMEM_BASE) == 0
