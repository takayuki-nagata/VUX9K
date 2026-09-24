# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu trap path (RISC-V mode): exceptions and the machine interrupts (timer;
external/software for their priority) enter mtvec with the right mcause/mepc/mtval,
a trapping instruction has no side effects, and mret returns to mepc.

Each program installs a handler that checks the trap CSRs itself and then spins at
label "ok" (or "bad" on any mismatch), so the tests only need to trace pc_out. They
use nothing but the CPU's ports and therefore also run on the gate-level netlist.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from rv32_asm import Asm

HANDLER = 0x200
MAX_CYCLES = 400


async def run_program(dut, words, *, irq_after=None, irq_lines=("timer_irq_in",), max_cycles=MAX_CYCLES):
    """Serve `words` as instruction memory (pc_out -> instr_in) and reset the CPU.

    Returns (PCs seen, one per cycle; whether mem_write was ever asserted).
    irq_after: raise the irq_lines inputs (default timer_irq_in) after that many cycles.
    """
    mem = {i * 4: w for i, w in enumerate(words)}
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig, val in (("soft_rst", 0), ("data_in", 0), ("timer_irq_in", 0), ("ext_irq_in", 0), ("sw_irq_in", 0)):
        getattr(dut, sig).value = val
    dut.rst.value = 0
    dut.instr_in.value = mem[0]
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    pcs = []
    wrote = False
    for cycle in range(max_cycles):
        await FallingEdge(dut.clk)
        pc = int(dut.pc_out.value)
        pcs.append(pc)
        wrote |= bool(int(dut.mem_write.value))
        dut.instr_in.value = mem.get(pc, 0x00000013)  # nop outside the program
        if irq_after is not None and cycle == irq_after:
            for line in irq_lines:
                getattr(dut, line).value = 1
    return pcs, wrote


def program_with_handler(body):
    a = Asm()
    a.li("t0", HANDLER)
    a.csrw("mtvec", "t0")
    body(a)
    a.label("spin")
    a.j("spin")
    words = a.assemble()
    words += [0x00000013] * (HANDLER // 4 - len(words))
    words.append(0x0000006F)  # handler: j .
    return words


def trap_program(body, cause, *, mtval=None, extra_checks=None):
    """body() must put label "insn" on the instruction expected to trap.

    The handler checks mcause == cause, mepc == insn and, if given, mtval against the
    value mtval(a, "t1") loads into t1; extra_checks(a) may branch to "bad" too.
    """
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    body(a)
    a.j("bad")  # fell through: no trap
    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", cause)
    a.bne("t0", "t1", "bad")
    a.csrr("t0", "mepc")
    a.la("t1", "insn")
    a.bne("t0", "t1", "bad")
    if mtval is not None:
        a.csrr("t0", "mtval")
        mtval(a, "t1")
        a.bne("t0", "t1", "bad")
    if extra_checks is not None:
        extra_checks(a)
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")
    return a.assemble(), a.labels


async def expect_ok(dut, program, **kwargs):
    words, labels = program
    pcs, wrote = await run_program(dut, words, **kwargs)
    last = [hex(p) for p in pcs[-4:]]
    assert labels["bad"] not in pcs, f"handler check failed (reached 'bad'); last PCs {last}"
    assert labels["ok"] in pcs, f"never reached 'ok'; last PCs {last}"
    return wrote


@cocotb.test()
async def test_harness_reaches_handler(dut):
    """Positive control: a plain jump to HANDLER is seen by the harness.

    If the instruction-memory model or the PC tracing were broken, the trap tests
    below could fail (or pass) for the wrong reason; this one must pass on its own.
    """

    def body(a):
        a.li("t1", HANDLER)
        a.jalr("zero", "t1", 0)

    pcs, _ = await run_program(dut, program_with_handler(body))
    assert HANDLER in pcs, f"harness never saw PC=0x{HANDLER:X}; last PCs {[hex(p) for p in pcs[-4:]]}"


@cocotb.test()
async def test_ecall_enters_mtvec(dut):
    """ECALL: mcause 11, mepc = the ecall"""

    def body(a):
        a.label("insn")
        a.ecall()

    await expect_ok(dut, trap_program(body, 11))


@cocotb.test()
async def test_ebreak_enters_mtvec(dut):
    """EBREAK: mcause 3, mepc = mtval = the ebreak"""

    def body(a):
        a.label("insn")
        a.ebreak()

    await expect_ok(dut, trap_program(body, 3, mtval=lambda a, r: a.la(r, "insn")))


@cocotb.test()
async def test_zero_word_is_illegal(dut):
    """0x00000000 is an illegal instruction: mcause 2, mtval = the instruction word (0)"""

    def body(a):
        a.label("insn")
        a.word(0x00000000)

    await expect_ok(dut, trap_program(body, 2, mtval=lambda a, r: a.li(r, 0)))


@cocotb.test()
async def test_unimp_is_illegal(dut):
    """`unimp` (csrrw x0, cycle, x0: write to a read-only CSR) is illegal, mtval = the word"""

    def body(a):
        a.label("insn")
        a.word(0xC0001073)

    await expect_ok(dut, trap_program(body, 2, mtval=lambda a, r: a.li(r, 0xC0001073)))


@cocotb.test()
async def test_misaligned_jalr_traps_without_link(dut):
    """jalr to a 2-byte-aligned target: mcause 0, mtval = target, rd not written"""

    def body(a):
        a.li("s2", 0)
        a.la("t2", "insn")
        a.addi("t2", "t2", 6)  # insn + 6 -> bit 1 set
        a.label("insn")
        a.jalr("s2", "t2", 0)

    def rd_untouched(a):
        a.bne("s2", "zero", "bad")

    def target(a, r):
        a.la(r, "insn")
        a.addi(r, r, 6)

    await expect_ok(dut, trap_program(body, 0, mtval=target, extra_checks=rd_untouched))


@cocotb.test()
async def test_misaligned_load_traps_without_writeback(dut):
    """lw from address 1: mcause 4, mtval = address, rd not written"""

    def body(a):
        a.li("s2", 0x55)
        a.label("insn")
        a.lw("s2", 1, "zero")

    def rd_untouched(a):
        a.li("t1", 0x55)
        a.bne("s2", "t1", "bad")

    await expect_ok(dut, trap_program(body, 4, mtval=lambda a, r: a.li(r, 1), extra_checks=rd_untouched))


@cocotb.test()
async def test_misaligned_store_traps_without_write(dut):
    """sw to address 0x102: mcause 6, mtval = address, and no memory write is issued"""

    def body(a):
        a.li("s2", 0x100)
        a.label("insn")
        a.sw("s2", 2, "s2")

    wrote = await expect_ok(dut, trap_program(body, 6, mtval=lambda a, r: a.li(r, 0x102)))
    assert not wrote, "a trapping store must not assert mem_write"


def timer_program(*, enable_mie):
    """Enable mie.MTIE (and mstatus.MIE if enable_mie), then count down in a loop.

    The handler checks mcause and that mepc lies inside the loop, records the
    interrupt in s1, masks it (timer_irq_in stays high) and returns with mret.
    After the loop, "ok"/"bad" depends on whether s1 matches enable_mie.
    """
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s1", 0)
    a.li("t0", 0x80)
    a.csrs("mie", "t0")
    if enable_mie:
        a.li("t0", 0x8)
        a.csrs("mstatus", "t0")
    a.li("t3", 60)
    a.label("loop")
    a.addi("t3", "t3", -1)
    a.label("loop_end")
    a.bnez("t3", "loop")
    if enable_mie:
        a.beqz("s1", "bad")
    else:
        a.bnez("s1", "bad")
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")

    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", 0x80000007)
    a.bne("t0", "t1", "bad")
    a.csrr("t0", "mepc")
    a.la("t1", "loop")
    a.bltu("t0", "t1", "bad")
    a.la("t1", "loop_end")
    a.bltu("t1", "t0", "bad")
    a.li("s1", 1)
    a.csrw("mie", "zero")
    a.mret()
    return a.assemble(), a.labels


@cocotb.test()
async def test_timer_interrupt_enters_mtvec_and_returns(dut):
    """With mie.MTIE and mstatus.MIE set, timer_irq_in traps (mcause 0x80000007) and mret resumes the loop"""
    await expect_ok(dut, timer_program(enable_mie=True), irq_after=40, max_cycles=600)


@cocotb.test()
async def test_timer_interrupt_masked_by_mstatus_mie(dut):
    """With mstatus.MIE clear, a pending enabled timer interrupt is not taken"""
    await expect_ok(dut, timer_program(enable_mie=False), irq_after=40, max_cycles=600)


def irq_priority_program():
    """Enable MEIE/MSIE/MTIE and mstatus.MIE, then count down in a loop.

    All three interrupt lines rise together and stay high. The handler checks that
    they are taken in priority order MEI > MSI > MTI (mcause 0x8000000b, 0x80000003,
    0x80000007), masking each one in mie before mret. After the loop, s1 must be 3.
    """
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s1", 0)
    a.li("t0", 0x888)  # MEIE | MTIE | MSIE
    a.csrs("mie", "t0")
    a.li("t0", 0x8)
    a.csrs("mstatus", "t0")
    a.li("t3", 80)
    a.label("loop")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "loop")
    a.li("t1", 3)
    a.bne("s1", "t1", "bad")
    a.label("ok")
    a.j("ok")
    a.label("bad")
    a.j("bad")

    a.label("handler")
    a.csrr("t0", "mcause")
    # (interrupts taken so far, expected mcause, mie afterwards)
    for taken, cause, mie_after in ((0, 0x8000000B, 0x88), (1, 0x80000003, 0x80), (2, 0x80000007, 0)):
        a.li("t1", taken)
        a.bne("s1", "t1", f"not_{taken}")
        a.li("t1", cause)
        a.bne("t0", "t1", "bad")
        a.li("t2", mie_after)
        a.csrw("mie", "t2")
        a.j("taken")
        a.label(f"not_{taken}")
    a.j("bad")  # a fourth interrupt
    a.label("taken")
    a.addi("s1", "s1", 1)
    a.mret()
    return a.assemble(), a.labels


@cocotb.test()
async def test_interrupt_priority_mei_msi_mti(dut):
    """Simultaneous external, software and timer interrupts are taken in order MEI > MSI > MTI"""
    await expect_ok(
        dut,
        irq_priority_program(),
        irq_after=40,
        irq_lines=("ext_irq_in", "sw_irq_in", "timer_irq_in"),
        max_cycles=900,
    )


@cocotb.test()
async def test_mret_returns_to_mepc(dut):
    """mret jumps to the address in mepc"""
    target = 0x180

    def body(a):
        a.li("t0", target)
        a.csrw("mepc", "t0")
        a.mret()

    words = program_with_handler(body)
    words[target // 4] = 0x0000006F  # target: j .
    pcs, _ = await run_program(dut, words)
    assert target in pcs, f"mret did not jump to mepc; last PCs {[hex(p) for p in pcs[-4:]]}"
