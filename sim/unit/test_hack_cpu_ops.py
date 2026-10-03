# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from unit_models import HACK_COMP


def make_hack_a(val: int) -> int:
    a = val & 0x7FFF
    return (a << 16) | a


def make_hack_c(a: int, c: int, d: int, j: int) -> int:
    val = 0xE000 | ((a & 1) << 12) | ((c & 0x3F) << 6) | ((d & 7) << 3) | (j & 7)
    return (val << 16) | val


@cocotb.test()
async def test_hack_cpu_comprehensive(dut):
    """Replicate hack_cpu/testbench/hack/cpu_test.vhd on unified_cpu (all ALU ops, dests, and jumps) in 2-cycle FSM"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # Reset in Hack Mode
    await FallingEdge(dut.clk)
    dut.soft_rst.value = 0
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.timer_irq_in.value = 0
    dut.mtime_in.value = 0
    dut.boot_mode_valid.value = 0
    dut.boot_mode.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0
    dut.instr_in.value = make_hack_a(1)  # First instruction @1

    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Cycle 1: Fetch -> Cycle 2: Execute
    await ClockCycles(dut.clk, 2)
    assert int(dut.active_mode.value) == 0, "Failed to enter Hack mode!"

    # 1. Test A-instruction: Load 0x1234 into A
    dut.instr_in.value = make_hack_a(0x1234)
    await ClockCycles(dut.clk, 2)

    # 2. Test D=A (c = 110000 = 0x30, d = 010 (D))
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)
    await ClockCycles(dut.clk, 2)

    # 3. Test A=10
    dut.instr_in.value = make_hack_a(10)
    await ClockCycles(dut.clk, 2)

    # 4. Test D=D+A (c = 000010 = 0x02, d = 010) -> D = 0x1234 + 10 = 0x123E
    dut.instr_in.value = make_hack_c(a=0, c=0x02, d=0b010, j=0)
    await ClockCycles(dut.clk, 2)

    # 5. Test AMD=D+1 (c = 011111 = 0x1F, d = 111 (A,M,D)) -> A=0x123F, D=0x123F, Mem[10]=0x123F
    dut.instr_in.value = make_hack_c(a=0, c=0x1F, d=0b111, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "Multi-dest AMD memory write failed"
    assert int(dut.data_addr.value) == 10, f"Expected store addr=10, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 0x123F, f"Expected store data=0x123F, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)
    await ClockCycles(dut.clk, 1)  # HACK_WR_D: with both A and D as destinations, D is written one cycle later

    # 6. Test Conditional Jump: JGT with D > 0 (D = 0x123F > 0 -> should jump to A = 0x123F)
    # PC should become (0x123F << 1) = 0x247E
    dut.instr_in.value = make_hack_c(a=0, c=0x0C, d=0b000, j=0b001)  # D;JGT
    await ClockCycles(dut.clk, 2)  # FETCH -> EXECUTE -> Update PC
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == (0x123F << 1), f"Expected jumped PC={0x123F << 1}, got {int(dut.pc_out.value)}"

    # 7. Test Read Memory M: D=M (with data_in = 0x55AA)
    dut.data_in.value = 0x55AA
    dut.instr_in.value = make_hack_c(a=1, c=0x30, d=0b010, j=0)  # D=M
    await ClockCycles(dut.clk, 2)  # FETCH -> EXECUTE -> MEM_WAIT
    await ClockCycles(dut.clk, 1)

    # 8. Test Read-Modify-Write Memory: M=M-1 (with A=10, data_in = 41)
    dut.instr_in.value = make_hack_a(10)
    await ClockCycles(dut.clk, 2)
    # M=M-1: a=1, c=110010=0x32, d=001 (M), j=0
    dut.instr_in.value = make_hack_c(a=1, c=0x32, d=0b001, j=0)
    # FETCH -> EXECUTE -> MEM_WAIT -> HACK_WB -> FETCH
    await ClockCycles(dut.clk, 1)  # Instruction pre-fetch cycle
    await Timer(1, unit="ns")
    dut.data_in.value = 41
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 0, "mem_write should be 0 during EXECUTE for M=M-1"
    await ClockCycles(dut.clk, 1)  # EXECUTE -> MEM_WAIT (latch data_in)
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 0, "mem_write should be 0 during MEM_WAIT for M=M-1 (read phase)"
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> HACK_WB (ALU compute and write to memory)
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "mem_write should be 1 during HACK_WB for M=M-1"
    assert int(dut.data_addr.value) == 10, f"Expected store addr=10, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 40, f"Expected store data=40, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)  # HACK_WB -> FETCH

    # 9. Test A-D: A=100, D=30, D=A-D -> D=70
    dut.instr_in.value = make_hack_a(30)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)  # D=A (D=30)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_a(100)
    await ClockCycles(dut.clk, 2)
    # D=A-D (a=0, c=0x07, d=010)
    dut.instr_in.value = make_hack_c(a=0, c=0x07, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 70, f"Expected A-D=70, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)  # EXECUTE -> FETCH
    # Store D to verify register write
    dut.instr_in.value = make_hack_a(20)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_c(a=0, c=0x0C, d=0b001, j=0)  # M=D
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 70, f"Expected stored D=70, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)

    # 10. Test M-D: with A=10, data_in=100, D=30 -> D=M-D -> D=70
    dut.instr_in.value = make_hack_a(30)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)  # D=30
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_a(10)
    await ClockCycles(dut.clk, 2)
    # D=M-D (a=1, c=0x07, d=010)
    dut.instr_in.value = make_hack_c(a=1, c=0x07, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    dut.data_in.value = 100
    await ClockCycles(dut.clk, 1)  # EXECUTE -> MEM_WAIT
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> HACK_WB (r_hack_data = 100, D = 30 -> ALU calculates M-D = 70)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 70, f"Expected M-D=70, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)  # HACK_WB -> FETCH (D written with 70)

    # 11. Test -A: A=42, D=-A -> D = (-42 & 0xFFFF) = 0xFFD6
    dut.instr_in.value = make_hack_a(42)
    await ClockCycles(dut.clk, 2)
    # D=-A (a=0, c=0x33, d=010)
    dut.instr_in.value = make_hack_c(a=0, c=0x33, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 0xFFD6, f"Expected -A=0xFFD6, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)

    # 12. Test -M: with A=10, data_in=42, D=-M -> D = 0xFFD6
    dut.instr_in.value = make_hack_a(10)
    await ClockCycles(dut.clk, 2)
    # D=-M (a=1, c=0x33, d=010)
    dut.instr_in.value = make_hack_c(a=1, c=0x33, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    dut.data_in.value = 42
    await ClockCycles(dut.clk, 1)  # EXECUTE -> MEM_WAIT
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> HACK_WB (r_hack_data = 42 -> ALU calculates 0-M = -42)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 0xFFD6, f"Expected -M=0xFFD6, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)  # HACK_WB -> FETCH

    # 13. Test -D: D=50, D=-D -> D = (-50 & 0xFFFF) = 0xFFCE
    dut.instr_in.value = make_hack_a(50)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)  # D=50
    await ClockCycles(dut.clk, 2)
    # D=-D (a=0, c=0x0F, d=010)
    dut.instr_in.value = make_hack_c(a=0, c=0x0F, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 0xFFCE, f"Expected -D=0xFFCE, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)

    # 14. Test !A: A=0x00FF, D=!A -> D = 0xFF00
    dut.instr_in.value = make_hack_a(0x00FF)
    await ClockCycles(dut.clk, 2)
    # D=!A (a=0, c=0x31, d=010)
    dut.instr_in.value = make_hack_c(a=0, c=0x31, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 0xFF00, f"Expected !A=0xFF00, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)

    # 15. Test !M: with A=10, data_in=0x00FF, D=!M -> D = 0xFF00
    dut.instr_in.value = make_hack_a(10)
    await ClockCycles(dut.clk, 2)
    # D=!M (a=1, c=0x31, d=010)
    dut.instr_in.value = make_hack_c(a=1, c=0x31, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    dut.data_in.value = 0x00FF
    await ClockCycles(dut.clk, 1)  # EXECUTE -> MEM_WAIT
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> HACK_WB (r_hack_data = 0x00FF -> ALU calculates ~M)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == 0xFF00, f"Expected !M=0xFF00, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)  # HACK_WB -> FETCH

    # 16. Test !D: D=0xAA55, D=!D -> D = 0x55AA
    dut.instr_in.value = make_hack_a(0x2A55)  # A-instr max 15-bit (0x2A55)
    await ClockCycles(dut.clk, 2)
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)  # D=0x2A55
    await ClockCycles(dut.clk, 2)
    # D=!D (a=0, c=0x0D, d=010)
    dut.instr_in.value = make_hack_c(a=0, c=0x0D, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    assert (int(dut.data_out.value) & 0xFFFF) == (0xFFFF ^ 0x2A55), (
        f"Expected !D={hex(0xFFFF ^ 0x2A55)}, got {hex(int(dut.data_out.value))}"
    )
    await ClockCycles(dut.clk, 1)

    dut._log.info("Comprehensive Hack CPU ops test passed [PASS]")


async def _reset_hack(dut):
    """Clock + reset with a Hack A-instruction first, so the CPU latches Hack mode."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await FallingEdge(dut.clk)
    for sig in (
        "soft_rst",
        "rst",
        "data_in",
        "timer_irq_in",
        "ext_irq_in",
        "sw_irq_in",
        "mtime_in",
        "boot_mode_valid",
        "boot_mode",
    ):
        getattr(dut, sig).value = 0
    dut.instr_in.value = make_hack_a(1)
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    assert int(dut.active_mode.value) == 0, "Failed to enter Hack mode!"


async def _exec(dut, instr):
    """One register-only Hack instruction (FETCH -> EXECUTE)."""
    dut.instr_in.value = instr
    await ClockCycles(dut.clk, 2)


async def _set_ad(dut, a, d):
    await _exec(dut, make_hack_a(d))
    await _exec(dut, make_hack_c(a=0, c=0x30, d=0b010, j=0))  # D=A
    await _exec(dut, make_hack_a(a))


async def _alu_a_form(dut, c):
    """M=<comp> with the A operand: the result is on data_out during EXECUTE."""
    dut.instr_in.value = make_hack_c(a=0, c=c, d=0b001, j=0)
    await ClockCycles(dut.clk, 1)
    await Timer(1, unit="ns")
    result = int(dut.data_out.value) & 0xFFFF
    await ClockCycles(dut.clk, 1)
    return result


async def _alu_m_form(dut, c, m):
    """D=<comp> with the M operand (data_in = m): the result is on data_out in HACK_WB."""
    dut.instr_in.value = make_hack_c(a=1, c=c, d=0b010, j=0)
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    dut.data_in.value = m
    await ClockCycles(dut.clk, 2)  # EXECUTE -> MEM_WAIT -> HACK_WB
    await Timer(1, unit="ns")
    result = int(dut.data_out.value) & 0xFFFF
    await ClockCycles(dut.clk, 1)  # HACK_WB -> FETCH
    return result


@cocotb.test()
async def test_hack_comp_const_one_or_xor(dut):
    """Comps the comprehensive test misses: 1, D|A, D|M and the D^A/D^M extension"""
    await _reset_hack(dut)
    d, a, m = 0x00F0, 0x00CC, 0x0A0A
    # (c bits, expected with A, expected with M) -- "1" ignores the a-bit
    for name, c, want_a, want_m in (
        ("1", 0b111111, 1, None),
        ("D|A/D|M", 0b010101, d | a, d | m),
        ("D^A/D^M", 0b000101, d ^ a, d ^ m),
    ):
        await _set_ad(dut, a, d)
        got = await _alu_a_form(dut, c)
        assert got == want_a, f"{name} (A form): expected {want_a:#06x}, got {got:#06x}"
        if want_m is not None:
            await _set_ad(dut, a, d)
            got = await _alu_m_form(dut, c, m)
            assert got == want_m, f"{name} (M form): expected {want_m:#06x}, got {got:#06x}"


@cocotb.test()
async def test_hack_jump_jle(dut):
    """D;JLE jumps for D < 0 and D == 0, and falls through for D > 0"""
    await _reset_hack(dut)
    target = 0x0123
    # (D, comp that loads it from A, taken)
    for label, a, comp, taken in (("D>0", 0x7FFF, 0x30, False), ("D=0", 0, 0x30, True), ("D<0", 0, 0x3A, True)):
        await _exec(dut, make_hack_a(a))
        await _exec(dut, make_hack_c(a=0, c=comp, d=0b010, j=0))  # D=A or D=-1
        await _exec(dut, make_hack_a(target))
        await _exec(dut, make_hack_c(a=0, c=0x0C, d=0b000, j=0b110))  # D;JLE
        await Timer(1, unit="ns")
        jumped = int(dut.pc_out.value) == (target << 1)
        assert jumped == taken, f"D;JLE with {label}: jumped={jumped}, expected {taken}"


async def _run_then_store_d(dut, instr, *, m=0, pulses):
    """Execute instr, then M=D; return the (data_addr, data_out) of each mem_write pulse.

    `pulses` is how many writes to wait for: instr's own M write (if it has one) and
    then M=D's, whose (data_addr, data_out) is (A, D) after instr. Counting pulses
    instead of cycles keeps this independent of how many cycles instr takes.
    """
    dut.data_in.value = m
    dut.instr_in.value = instr
    await ClockCycles(dut.clk, 1)  # FETCH latches instr
    dut.instr_in.value = make_hack_c(a=0, c=0x0C, d=0b001, j=0)  # M=D, fetched once instr is done
    writes, prev = [], 0
    for _ in range(20):
        await FallingEdge(dut.clk)
        now = int(dut.mem_write.value)
        if now and not prev:
            writes.append((int(dut.data_addr.value) & 0xFFFF, int(dut.data_out.value) & 0xFFFF))
            if len(writes) == pulses:
                break
        prev = now
    assert len(writes) == pulses, f"saw {len(writes)} memory writes, expected {pulses}"
    await ClockCycles(dut.clk, 1)
    return writes


@cocotb.test()
async def test_hack_dual_register_dest(dut):
    """AD=/AMD= write both A and D (and M at the old A), from the A and the M form of the comp"""
    await _reset_hack(dut)
    # (label, instruction, M operand, whether it writes M, expected result)
    cases = (
        ("AD=D+1", make_hack_c(a=0, c=0x1F, d=0b110, j=0), 0, False, 11),
        ("AMD=D+1", make_hack_c(a=0, c=0x1F, d=0b111, j=0), 0, True, 11),
        ("AMD=M+1", make_hack_c(a=1, c=0x37, d=0b111, j=0), 20, True, 21),
        ("AD=A+1", make_hack_c(a=0, c=0x37, d=0b110, j=0), 0, False, 6),
    )
    for label, instr, m, writes_m, result in cases:
        await _set_ad(dut, a=5, d=10)
        writes = await _run_then_store_d(dut, instr, m=m, pulses=2 if writes_m else 1)
        if writes_m:
            assert writes[0] == (5, result), f"{label}: M write (addr, data) {writes[0]}, expected (5, {result})"
        a, d = writes[-1]
        assert (a, d) == (result, result), f"{label}: afterwards A={a}, D={d}; expected both {result}"


@cocotb.test()
async def test_hack_dual_dest_jump_uses_old_a(dut):
    """AD=D+1;JMP jumps to A as it was before the instruction, and fetches from there next"""
    await _reset_hack(dut)
    await _set_ad(dut, a=5, d=10)
    dut.instr_in.value = make_hack_c(a=0, c=0x1F, d=0b110, j=0b111)  # AD=D+1;JMP
    await ClockCycles(dut.clk, 2)  # FETCH, EXECUTE (PC <- old A), now in the D write-back cycle
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 5 << 1, f"jumped to {int(dut.pc_out.value):#x}, expected old A (5) << 1"
    await ClockCycles(dut.clk, 1)  # D write-back cycle -> FETCH
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 5 << 1, "the instruction after the jump must be fetched from the target"


def hack_c(a, comp, dest, jump):
    return 0xE000 | a << 12 | comp << 6 | dest << 3 | jump


def random_hack_program(length):
    """A random Hack program: @n and C-instructions with every comp/dest, M accesses, and
    forward conditional jumps (@target; comp;Jxx), ending in an @END; 0;JMP loop.
    Starts with @1 (so the CPU detects Hack) and D=0: registers have no reset."""
    prog, jumps = [1, hack_c(0, 0b101010, 0b010, 0)], []
    comps = list(HACK_COMP)
    while len(prog) < length:
        r = random.random()
        if r < 0.3:
            prog.append(random.getrandbits(4) if random.random() < 0.6 else random.getrandbits(15))
        elif r < 0.85:
            prog.append(hack_c(random.getrandbits(1), random.choice(comps), random.randrange(8), 0))
        else:
            jumps.append(len(prog))
            jump = hack_c(random.getrandbits(1), random.choice(comps), random.randrange(8), random.randrange(1, 8))
            prog += [0, jump]
    end = len(prog)
    # The @ before each jump: a later instruction, but never another pair's jump (that
    # would jump with A still holding its own address: a loop)
    landing = [t for t in range(end + 1) if t - 1 not in jumps]
    for i in jumps:
        prog[i] = random.choice([t for t in landing if t >= i + 2])
    return prog + [end, hack_c(0, 0b101010, 0, 0b111)]


def run_hack(prog, initial):
    """Hack interpreter: the (address, value) of each memory write, 16-bit. initial(addr)
    is what memory holds before the program writes it."""
    a = d = pc = 0  # (A is set by the first instruction, D by the second)
    writes: list = []
    ram: dict[int, int] = {}
    for _ in range(len(prog)):  # forward jumps only: never more steps than instructions
        if pc == len(prog) - 2:
            break
        ins = prog[pc]
        if not ins & 0x8000:
            a, pc = ins, pc + 1
            continue
        y = ram.get(a, initial(a)) if ins >> 12 & 1 else a
        r = HACK_COMP[ins >> 6 & 0x3F](d, y) & 0xFFFF
        neg, zero = bool(r & 0x8000), r == 0
        take = (ins & 4 and neg) or (ins & 2 and zero) or (ins & 1 and not neg and not zero)
        if ins & 8:
            writes.append((a, r))
            ram[a] = r
        target = a  # a jump goes to A as it was before the instruction
        if ins & 0x20:
            a = r
        if ins & 0x10:
            d = r
        pc = target if take else pc + 1
    assert pc == len(prog) - 2, f"generated program doesn't reach END: {[hex(i) for i in prog]}"
    return writes


@cocotb.test()
async def test_random_programs_against_interpreter(dut):
    """Random Hack programs on unified_cpu behind a memory model with soc_ram's timing
    (both ports read one cycle after the address, before that edge's write): the memory
    writes, in order, match a Hack interpreter's"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig in ("soft_rst", "timer_irq_in", "mtime_in", "boot_mode_valid", "boot_mode", "ext_irq_in", "sw_irq_in"):
        getattr(dut, sig).value = 0
    for _ in range(25):
        prog = random_hack_program(random.randint(20, 80))
        words = [prog[i] | (prog[i + 1] if i + 1 < len(prog) else 0) << 16 for i in range(0, len(prog), 2)]
        init: dict[int, int] = {}  # memory before the program writes it: a fixed random value per address

        def initial(addr, init=init):
            return init.setdefault(addr, random.getrandbits(16))

        expected = run_hack(prog, initial)
        ram: dict[int, int] = {}

        dut.rst.value = 0
        dut.instr_in.value = words[0]
        dut.data_in.value = 0
        await ClockCycles(dut.clk, 2)
        await FallingEdge(dut.clk)
        dut.rst.value = 1
        pending = (words[0], 0)  # what the RAM shows in the next cycle
        writes = []
        for _ in range(8 * len(prog) * 4):
            await FallingEdge(dut.clk)  # mid-cycle: the CPU's outputs for the coming edge
            pc, daddr = int(dut.pc_out.value), int(dut.data_addr.value) & 0xFFFF
            we, wdata = int(dut.mem_write.value), int(dut.data_out.value) & 0xFFFF
            dut.instr_in.value, dut.data_in.value = pending
            pending = (words[pc >> 2] if pc >> 2 < len(words) else 0, ram.get(daddr, initial(daddr)))
            if we:
                ram[daddr] = wdata
                writes.append((daddr, wdata))
        assert int(dut.active_mode.value) == 0, "not in Hack mode"
        assert int(dut.pc_out.value) >> 1 in (len(prog) - 2, len(prog) - 1), "did not reach the END loop"
        first = next((i for i, (g, e) in enumerate(zip(writes, expected, strict=False)) if g != e), None)
        assert writes == expected, (
            f"memory writes differ at #{first} of {len(expected)} (got {len(writes)}): "
            f"got {writes[first : first + 3] if first is not None else writes[len(expected) :][:3]}, "
            f"expected {expected[first : first + 3] if first is not None else expected[len(writes) :][:3]}; "
            f"program {[hex(i) for i in prog]}"
        )
