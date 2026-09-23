# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer


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
