# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Emulator side of the RTL <-> emulator lockstep check (sim/emu/test_lockstep.py).

sim/integration/test_soc_lockstep.py traces each program of lockstep_programs.py on
the RTL (tb_soc_top.sv's VUX9K_RTL_TRACE block). This test runs the same programs on
the emulator and compares, instruction by instruction: the FETCH cycle (so every
instruction's cycle count), ISA, PC, register writes, bus writes and trap causes;
then every UART TX byte with the cycle its start bit began. The first difference
fails the test, with both sides' instructions around it in
build/sim/diff/<program>.txt.

`make sim-lockstep` produces the trace and runs this; LOCKSTEP_TRACE names the trace
file. Without one (e.g. plain `make test-emu`) the test is skipped.
"""

import bisect
import os
import sys
from collections import namedtuple

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vux9k import REPO_ROOT, start_soc  # noqa: E402 (puts sim/integration on sys.path)
from lockstep_programs import UART_BIT, UART_FRAME, programs  # noqa: E402

TRACE = os.environ.get("LOCKSTEP_TRACE", "")
DIFF_DIR = os.path.join(REPO_ROOT, "build", "sim", "diff")

Instr = namedtuple("Instr", "cycle riscv pc regs writes trap")


def _parse_trace(path):
    """{program id: ([Instr], [(cycle, uart_tx level)])} from tb_soc_top's trace."""
    progs = {}
    instrs = uart = modes = None
    cur = None

    def finish():
        # An instruction runs in the ISA active_mode has in the cycle after its FETCH
        if instrs is None:
            return
        cycles = [c for c, _ in modes]
        for k, i in enumerate(instrs):
            m = bisect.bisect_right(cycles, i.cycle + 1)
            instrs[k] = i._replace(riscv=modes[m - 1][1] if m else True)

    with open(path) as f:
        for line in f:
            kind, *v = line.split()
            if kind == "P":
                finish()
                instrs, uart, modes = [], [], []
                progs[int(v[0], 16)] = (instrs, uart)
                cur = None
                continue
            c = int(v[0], 16)
            if kind == "F":
                cur = Instr(c, None, int(v[1], 16), [], [], [])
                instrs.append(cur)
            elif kind == "M":
                modes.append((c, v[1] == "1"))
            elif kind == "R":
                rd, val = int(v[1], 16), int(v[2], 16)
                if rd != 0 and cur is not None:
                    cur.regs.append((rd, val))
            elif kind == "W" and cur is not None:
                cur.writes.append((int(v[1], 16), int(v[2], 16), int(v[3], 16)))
            elif kind == "T" and cur is not None:
                cur.trap.append(int(v[1], 16))
            elif kind == "U":
                uart.append((c, int(v[1], 16)))
    finish()
    return progs


def _decode_uart(changes, end_cycle):
    """TX bytes (byte, start cycle) from pin changes, complete frames only."""
    out = []
    cycles = [c for c, _ in changes]

    def level(c):
        i = bisect.bisect_right(cycles, c)
        return changes[i - 1][1] if i else 1

    starts = [c for c, v in changes if v == 0]
    free = -1
    for s in starts:
        if s < free:
            continue
        if s + UART_FRAME > end_cycle:
            break
        byte = sum(level(s + UART_BIT * (i + 1) + UART_BIT // 2) << i for i in range(8))
        out.append((byte, s))
        free = s + 9 * UART_BIT + UART_BIT // 2
    return out


def _emu_instr(r):
    regs = [x for x in (r["rd"], r["rd2"]) if x is not None and x[0] != 0]
    writes = [r["store"]] if r["store"] is not None else []
    trap = [r["trap"]] if r["trap"] is not None else []
    return Instr(r["cycle"], r["riscv"], r["pc"], regs, writes, trap)


def _fmt(i):
    if i is None:
        return "-"
    s = f"{i.cycle:>9} {'rv' if i.riscv else 'hk'} {i.pc:08x}"
    s += "".join(f" x{rd}={v:08x}" for rd, v in i.regs)
    s += "".join(f" [{a:08x}]={d:08x}/{be:x}" for a, d, be in i.writes)
    s += "".join(f" trap={t:x}" for t in i.trap)
    return s


def _diff_report(name, rtl, emu, k):
    os.makedirs(DIFF_DIR, exist_ok=True)
    path = os.path.join(DIFF_DIR, f"{name}.txt")
    lo, hi = max(0, k - 20), k + 5
    with open(path, "w") as f:
        f.write(f"# {name}: first difference at instruction {k}\n# {'RTL':<60} | emulator\n")
        for j in range(lo, hi):
            a = rtl[j] if j < len(rtl) else None
            b = emu[j] if j < len(emu) else None
            mark = ">>" if j == k else "  "
            f.write(f"{mark} {j:>7} {_fmt(a):<60} | {_fmt(b)}\n")
    return path


PROGRAMS = list(enumerate(programs(), 1))


@pytest.fixture(scope="module")
def trace():
    if not TRACE:
        pytest.skip("no RTL trace (LOCKSTEP_TRACE); run `make sim-lockstep`")
    assert os.path.exists(TRACE), f"{TRACE} missing"
    return _parse_trace(TRACE)


@pytest.mark.parametrize("pid,prog", PROGRAMS, ids=[p.name for _, p in PROGRAMS])
def test_lockstep(trace, pid, prog):
    if pid not in trace:
        pytest.skip(f"{prog.name} not in the trace (slow program?)")
    rtl, rtl_uart = trace[pid]
    assert rtl, f"{prog.name}: the RTL trace has no instructions"

    soc = start_soc(sd_sectors=prog.sd_sectors, imem_words=prog.imem_words)
    for cycle, data in prog.uart_rx:
        soc.uart_send(data, at=cycle)
    buttons = sorted(prog.button)
    emu = []
    while soc.cycle < prog.cycles:
        # A button change is applied shortly before it matters (the GPIO keeps one
        # pending edge); reads look 2 cycles back through the synchronizer
        while buttons and buttons[0][0] <= soc.cycle + 8:
            c, pressed = buttons.pop(0)
            soc.set_button(pressed, at=c)
        emu.append(_emu_instr(soc.step()))

    # The RTL trace ends mid-instruction at prog.cycles: compare what both completed
    n = min(len(rtl), len(emu))
    while n and (rtl[n - 1].cycle >= prog.cycles - 8 or emu[n - 1].cycle >= prog.cycles - 8):
        n -= 1
    for k in range(n):
        a, b = rtl[k], emu[k]
        same = (a.cycle, a.riscv, a.pc, a.trap) == (b.cycle, b.riscv, b.pc, b.trap)
        same = same and sorted(a.regs) == sorted(b.regs) and a.writes == b.writes
        if not same:
            path = _diff_report(prog.name, rtl, emu, k)
            pytest.fail(f"{prog.name}: instruction {k} differs\n  RTL {_fmt(a)}\n  emu {_fmt(b)}\n  (see {path})")
    assert abs(len(rtl) - len(emu)) <= 2, f"{prog.name}: RTL {len(rtl)} vs emulator {len(emu)} instructions"

    end = prog.cycles - UART_FRAME
    rtl_tx = [x for x in _decode_uart(rtl_uart, prog.cycles) if x[1] < end]
    emu_tx = [x for x in soc.uart_tx_log() if x[1] < end]
    assert rtl_tx == emu_tx, f"{prog.name}: UART TX differs\n  RTL {rtl_tx[:20]}\n  emu {emu_tx[:20]}"
