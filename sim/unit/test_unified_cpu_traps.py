# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
unified_cpu trap path (RISC-V mode): ECALL and the machine timer interrupt must enter
mtvec, and mret must return to mepc.

unified_cpu currently takes no traps at all -- the ECALL/EBREAK/MRET/interrupt decode
was lost in commit 02a105a (see AGENTS.md) -- so every test here is expect_fail. When
the trap path is restored, cocotb reports these as failures ("passed unexpectedly")
until expect_fail is removed.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from rv32_asm import Asm

HANDLER = 0x200
MAX_CYCLES = 400


async def run_program(dut, words, *, irq_after=None):
    """Serve `words` as instruction memory (pc_out -> instr_in) and reset the CPU.

    Returns a list of the PCs seen, one per cycle. irq_after: raise timer_irq_in
    after that many cycles.
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
    for cycle in range(MAX_CYCLES):
        await FallingEdge(dut.clk)
        pc = int(dut.pc_out.value)
        pcs.append(pc)
        dut.instr_in.value = mem.get(pc, 0x00000013)  # nop outside the program
        if irq_after is not None and cycle == irq_after:
            dut.timer_irq_in.value = 1
    return pcs


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


@cocotb.test()
async def test_harness_reaches_handler(dut):
    """Positive control: a plain jump to HANDLER is seen by the harness.

    The expect_fail tests below would also "pass" if the instruction-memory model or
    the PC tracing were broken; this one must genuinely pass for them to mean anything.
    """

    def body(a):
        a.li("t1", HANDLER)
        a.jalr("zero", "t1", 0)

    pcs = await run_program(dut, program_with_handler(body))
    assert HANDLER in pcs, f"harness never saw PC=0x{HANDLER:X}; last PCs {[hex(p) for p in pcs[-4:]]}"


@cocotb.test(expect_fail=True)
async def test_ecall_enters_mtvec(dut):
    """ECALL jumps to mtvec"""
    words = program_with_handler(lambda a: a._emit(0x00000073))  # ecall
    pcs = await run_program(dut, words)
    assert HANDLER in pcs, f"never reached the trap handler; last PCs {[hex(p) for p in pcs[-4:]]}"


@cocotb.test(expect_fail=True)
async def test_timer_interrupt_enters_mtvec(dut):
    """With mie.MTIE and mstatus.MIE set, timer_irq_in traps to mtvec"""

    def body(a):
        a.li("t0", 0x80)
        a.csrs("mie", "t0")
        a.li("t0", 0x8)
        a.csrs("mstatus", "t0")

    pcs = await run_program(dut, program_with_handler(body), irq_after=100)
    assert HANDLER in pcs[100:], f"timer interrupt not taken; last PCs {[hex(p) for p in pcs[-4:]]}"


@cocotb.test(expect_fail=True)
async def test_mret_returns_to_mepc(dut):
    """mret jumps to the address in mepc"""
    target = 0x180

    def body(a):
        a.li("t0", target)
        a.csrw("mepc", "t0")
        a.mret()

    words = program_with_handler(body)
    words[target // 4] = 0x0000006F  # target: j .
    pcs = await run_program(dut, words)
    assert target in pcs, f"mret did not jump to mepc; last PCs {[hex(p) for p in pcs[-4:]]}"
