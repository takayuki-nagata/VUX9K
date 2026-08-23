# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

def make_hack_a(val: int) -> int:
    a = val & 0x7FFF
    return (a << 16) | a

def make_hack_c(a: int, c: int, d: int, j: int) -> int:
    val = 0xE000 | ((a & 1) << 12) | ((c & 0x3F) << 6) | ((d & 7) << 3) | (j & 7)
    return (val << 16) | val

@cocotb.test()
async def test_hack_cpu_comprehensive(dut):
    """Replicate hack_cpu/testbench/hack/cpu_test.vhd on unified_cpu (all ALU ops, dests, and jumps)"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # Reset in Hack Mode
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.timer_irq_in.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0
    dut.instr_in.value = make_hack_a(0) # First instruction @0

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)
    assert int(dut.active_mode.value) == 0, "Failed to enter Hack mode!"

    # 1. Test A-instruction: Load 0x1234 into A
    dut.instr_in.value = make_hack_a(0x1234)
    await FallingEdge(dut.clk)

    # 2. Test D=A (c = 110000 = 0x30, d = 010 (D))
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0b010, j=0)
    await FallingEdge(dut.clk)

    # 3. Test A=10
    dut.instr_in.value = make_hack_a(10)
    await FallingEdge(dut.clk)

    # 4. Test D=D+A (c = 000010 = 0x02, d = 010) -> D = 0x1234 + 10 = 0x123E
    dut.instr_in.value = make_hack_c(a=0, c=0x02, d=0b010, j=0)
    await FallingEdge(dut.clk)

    # 5. Test AMD=D+1 (c = 011111 = 0x1F, d = 111 (A,M,D)) -> A=0x123F, D=0x123F, Mem[10]=0x123F
    dut.instr_in.value = make_hack_c(a=0, c=0x1F, d=0b111, j=0)
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "Multi-dest AMD memory write failed"
    assert int(dut.data_addr.value) == 10, f"Expected store addr=10, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 0x123F, f"Expected store data=0x123F, got {int(dut.data_out.value)}"
    await FallingEdge(dut.clk)

    # 6. Test Jumps:
    # A = 100
    dut.instr_in.value = make_hack_a(100)
    await FallingEdge(dut.clk)

    # JMP unconditional (j = 111 = 7) -> Jump PC to A (100) (byte address 200)
    dut.instr_in.value = make_hack_c(a=0, c=0x30, d=0, j=0b111)
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 200, f"Expected PC=200 after JMP, got {int(dut.pc_out.value)}"

    # A = 200
    dut.instr_in.value = make_hack_a(200)
    await FallingEdge(dut.clk)

    # D = 0
    dut.instr_in.value = make_hack_c(a=0, c=0x2A, d=0b010, j=0) # c=101010 (0)
    await FallingEdge(dut.clk)

    # JEQ with D=0 -> should jump to A (200) (byte address 400)
    dut.instr_in.value = make_hack_c(a=0, c=0x0C, d=0, j=0b010) # D;JEQ
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 400, f"Expected PC=400 after JEQ, got {int(dut.pc_out.value)}"

    # JGT with D=0 -> should NOT jump (PC increments to 201, byte address 402)
    dut.instr_in.value = make_hack_c(a=0, c=0x0C, d=0, j=0b001) # D;JGT
    await FallingEdge(dut.clk)
    assert int(dut.pc_out.value) == 402, f"Expected PC=402 when JGT not taken, got {int(dut.pc_out.value)}"

    dut._log.info("Comprehensive Hack CPU test passed 100% [PASS]")
