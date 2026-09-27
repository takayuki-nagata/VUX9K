# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
cocotb side of the riscv-tests harness (sim/tb_hex_runner.veryl), run by
scripts/run_riscv_tests.py once per test, in a run directory holding the test's
program.hex.

Drives clock and reset, waits for the `tohost` write (or MAX_CYCLES) with one
trigger rather than a per-cycle loop, and writes the verdict line to verdict.txt:
"[PASS] tohost=1 ...", "[FAIL] tohost=0x... (TESTNUM=n) ..." or "[FAIL] ... timeout ...".
The cocotb test itself passes whatever the verdict; it only fails if the harness breaks.

Environment: MAX_CYCLES (default 2000000); TRACE=1 logs PC/instruction every cycle.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.simtime import get_sim_time
from cocotb.triggers import ClockCycles, FallingEdge, First, ReadOnly, RisingEdge, Timer

PERIOD_NS = 20  # arbitrary; only cycles matter


async def trace(dut, start_ns):
    while True:
        await FallingEdge(dut.clk)
        cycle = int((get_sim_time("ns") - start_ns) // PERIOD_NS)
        dut._log.info(f"[TRACE] {cycle} PC={int(dut.pc_out.value):08x} INSTR={int(dut.instr_in.value):08x}")


def write_verdict(line):
    # Once, after the simulation has ended: nothing left for a blocking write to stall
    with open("verdict.txt", "w") as f:
        f.write(line + "\n")


@cocotb.test()
async def run_program(dut):
    max_cycles = int(os.environ.get("MAX_CYCLES", "2000000"))
    cocotb.start_soon(Clock(dut.clk, PERIOD_NS, unit="ns").start())

    dut.rst.value = 0  # active-low
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    start_ns = get_sim_time("ns")
    if os.environ.get("TRACE", "") not in ("", "0"):
        cocotb.start_soon(trace(dut, start_ns))

    await First(RisingEdge(dut.halted), Timer(max_cycles * PERIOD_NS, unit="ns"))
    # halt_code is written on the same edge as halted, but cocotb resumes on halted's
    # change before that update lands: read both once the time step has settled
    await ReadOnly()
    cycles = int((get_sim_time("ns") - start_ns) // PERIOD_NS)
    if dut.halted.value == 1:
        code = int(dut.halt_code.value)
        if code == 1:
            verdict = f"[PASS] tohost=1 at cycle {cycles}"
        else:
            verdict = f"[FAIL] tohost=0x{code:08x} (TESTNUM={code >> 1}) at cycle {cycles}"
    else:
        verdict = f"[FAIL] Simulation timeout after {max_cycles} cycles at PC={int(dut.pc_out.value):08x}"
    dut._log.info(verdict)
    write_verdict(verdict)
