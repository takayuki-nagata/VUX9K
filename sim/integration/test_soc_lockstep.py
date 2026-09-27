# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
RTL side of the RTL <-> emulator lockstep check (test_soc_lockstep.py).

Runs every program of lockstep_programs.py on soc_top with tb_soc_top's trace on; one
simulation, one reset per program, stimulus at exact cycles. The comparison against
the emulator is sim/emu/test_lockstep.py (`make sim-lockstep` runs both), so the RTL
tests never depend on the Rust build. LOCKSTEP_SLOW=1 adds the long programs.
"""

import os

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, Timer
from lockstep_programs import UART_BIT, programs
from soc_env import CLK_PERIOD_PS, REPO_ROOT, load_imem, read_hex_words, soc_of, start_soc

SLOW = os.environ.get("LOCKSTEP_SLOW", "") not in ("", "0")


def _firmware():
    base = os.path.join(REPO_ROOT, "build", "firmware")
    iram = read_hex_words(os.path.join(base, "firmware.hex"))
    lanes = [read_hex_words(os.path.join(base, f"firmware_d{i}.hex")) for i in range(4)]
    return iram, lanes


def _power_on_memories(soc, imem_words):
    """What $readmemh loaded at time 0 (earlier programs changed it), plus the program."""
    iram, lanes = _firmware()
    load_imem(soc, iram)
    if imem_words:
        load_imem(soc, imem_words)
    for lane, words in enumerate(lanes):
        mem = getattr(soc.ram_inst, f"d_mem{lane}")
        for i, v in enumerate(words):
            mem[i].value = v
    regs = soc.cpu_inst.reg_file.registers
    for i in range(32):
        regs[i].value = 0  # no reset in rv32i_regfile; the emulator starts from 0


async def _now(dut) -> int:
    """Current cycle number (tb_soc_top's trace counter), at a clock edge."""
    await RisingEdge(dut.soc.clk)
    await ReadOnly()
    return int(dut.cyc.value)


async def _at_cycle(dut, cycle):
    """Wait until a quarter period into `cycle`: a pin written now is seen in that cycle."""
    now = await _now(dut)
    assert cycle > now, f"stimulus for cycle {cycle} comes too late (now {now})"
    await Timer((cycle - now) * CLK_PERIOD_PS + CLK_PERIOD_PS // 4, unit="ps")


async def _drive(dut, prog):
    events = []  # (cycle, pin, level)
    line_free = 0
    for cycle, data in prog.uart_rx:
        start = max(cycle, line_free)
        for byte in data:
            bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
            events += [(start + k * UART_BIT, "uart_rx", b) for k, b in enumerate(bits)]
            start += 10 * UART_BIT
        line_free = start
    events += [(c, "btn", 0 if pressed else 1) for c, pressed in prog.button]
    for cycle, pin, level in sorted(events):
        await _at_cycle(dut, cycle)
        getattr(dut, pin).value = level


@cocotb.test()
async def test_lockstep_trace(dut):
    """Trace every lockstep program on the RTL (compared by sim/emu/test_lockstep.py)"""
    for pid, prog in enumerate(programs(), 1):
        if prog.slow and not SLOW:
            continue
        dut._log.info(f"lockstep program {pid}: {prog.name} ({prog.cycles} cycles)")
        dut.trace_id.value = pid
        await start_soc(
            dut,
            sd_sectors=prog.sd_sectors,
            during_reset=lambda soc, prog=prog: _power_on_memories(soc, prog.imem_words),
        )
        driver = cocotb.start_soon(_drive(dut, prog))
        await _at_cycle(dut, prog.cycles)
        driver.cancel()
        # Hold reset until the next program: nothing more gets traced for this one
        dut.rst_n.value = 0
        await Timer(10 * CLK_PERIOD_PS, unit="ps")
    assert soc_of(dut).raw_rst.value == 0
