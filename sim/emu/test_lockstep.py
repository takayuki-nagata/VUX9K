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
from collections import deque, namedtuple

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integration"))
from lockstep_programs import UART_BIT, UART_FRAME, XMEM_WAIT, programs  # noqa: E402
from vux9k import REPO_ROOT, start_soc  # noqa: E402

TRACE = os.environ.get("LOCKSTEP_TRACE", "")
DIFF_DIR = os.path.join(REPO_ROOT, "build", "sim", "diff")

Instr = namedtuple("Instr", "cycle riscv pc regs writes trap")
# Longest instruction in cycles: a window load fetched from the window (2 FETCH/EXECUTE + 1
# MEM_WAIT + two waits of soc_pkg::XMEM_READ_LATENCY), with room to spare
LONGEST = 3 + 2 * XMEM_WAIT + 7


def _index_trace(path):
    """{program id: (start, end)}: each program's byte range in tb_soc_top's trace."""
    starts = {}
    index = {}
    pos = 0
    cur = None
    with open(path, "rb") as f:
        for line in f:
            if line.startswith(b"P "):
                if cur is not None:
                    index[cur] = (starts[cur], pos)
                cur = int(line.split()[1], 16)
                starts[cur] = pos + len(line)
            pos += len(line)
    if cur is not None:
        index[cur] = (starts[cur], pos)
    return index


def _rtl_instrs(path, span, uart):
    """One program's RTL instructions in order, its UART pin changes appended to uart.

    One at a time: the slow programs have millions (~450 B each as Instr), more than a CI
    host's memory holds for both sides. The trace is written cycle by cycle, so when the next
    F comes every M up to the cycle after this FETCH, which sets its ISA, has been read.
    """
    modes: list = []  # (cycle, riscv): the mode changes, a handful per program
    cur = None

    def done(i):
        m = bisect.bisect_right(modes, (i.cycle + 1, True))
        return i._replace(riscv=modes[m - 1][1] if m else True)

    start, end = span
    with open(path, "rb") as f:
        f.seek(start)
        pos = start
        for line in f:
            pos += len(line)
            if pos > end:
                break
            kind, *v = line.split()
            c = int(v[0], 16)
            if kind == b"F":
                if cur is not None:
                    yield done(cur)
                cur = Instr(c, None, int(v[1], 16), [], [], [])
            elif kind == b"M":
                modes.append((c, v[1] == b"1"))
            elif kind == b"R":
                rd, val = int(v[1], 16), int(v[2], 16)
                if rd != 0 and cur is not None:
                    cur.regs.append((rd, val))
            elif kind == b"W" and cur is not None:
                cur.writes.append((int(v[1], 16), int(v[2], 16), int(v[3], 16)))
            elif kind == b"T" and cur is not None:
                cur.trap.append(int(v[1], 16))
            elif kind == b"U":
                uart.append((c, int(v[1], 16)))
    if cur is not None:
        yield done(cur)


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


def _diff_report(name, rtl, emu, lo, k):
    """rtl/emu: the instructions from number lo on, up to a few past the difference at k."""
    os.makedirs(DIFF_DIR, exist_ok=True)
    path = os.path.join(DIFF_DIR, f"{name}.txt")
    with open(path, "w") as f:
        f.write(f"# {name}: first difference at instruction {k}\n# {'RTL':<60} | emulator\n")
        for j in range(lo, lo + max(len(rtl), len(emu))):
            a = rtl[j - lo] if j - lo < len(rtl) else None
            b = emu[j - lo] if j - lo < len(emu) else None
            mark = ">>" if j == k else "  "
            f.write(f"{mark} {j:>7} {_fmt(a):<60} | {_fmt(b)}\n")
    return path


PROGRAMS = list(enumerate(programs(), 1))


@pytest.fixture(scope="module")
def trace():
    if not TRACE:
        pytest.skip("no RTL trace (LOCKSTEP_TRACE); run `make sim-lockstep`")
    assert os.path.exists(TRACE), f"{TRACE} missing"
    return _index_trace(TRACE)


@pytest.mark.parametrize("pid,prog", PROGRAMS, ids=[p.name for _, p in PROGRAMS])
def test_lockstep(trace, pid, prog):
    if pid not in trace:
        pytest.skip(f"{prog.name} not in the trace (slow program?)")
    rtl_uart: list = []
    rtl = _rtl_instrs(TRACE, trace[pid], rtl_uart)

    soc = start_soc(sd_sectors=prog.sd_sectors, imem_words=prog.imem_words)
    for cycle, data in prog.uart_rx:
        soc.uart_send(data, at=cycle)
    buttons = sorted(prog.button)

    def emu_instrs():
        while soc.cycle < prog.cycles:
            # A button change is applied shortly before it matters (the GPIO keeps one
            # pending edge); reads look 2 cycles back through the synchronizer
            while buttons and buttons[0][0] <= soc.cycle + 8:
                c, pressed = buttons.pop(0)
                soc.set_button(pressed, at=c)
            yield _emu_instr(soc.step())

    emu = emu_instrs()
    # The RTL trace ends mid-instruction at prog.cycles: compare what both completed. Both
    # sides are streamed (a slow program has millions of instructions); the last few pairs
    # are kept for the report
    limit = prog.cycles - LONGEST
    recent: deque = deque(maxlen=20)
    k = n_rtl = n_emu = 0
    while True:
        a, b = next(rtl, None), next(emu, None)
        n_rtl += a is not None
        n_emu += b is not None
        if a is None or b is None or a.cycle >= limit or b.cycle >= limit:
            break
        same = (a.cycle, a.riscv, a.pc, a.trap) == (b.cycle, b.riscv, b.pc, b.trap)
        same = same and sorted(a.regs) == sorted(b.regs) and a.writes == b.writes
        if not same:
            more = [(next(rtl, None), next(emu, None)) for _ in range(4)]
            pairs = [*recent, (a, b), *more]
            rtl_win = [x for x, _ in pairs if x is not None]
            emu_win = [y for _, y in pairs if y is not None]
            path = _diff_report(prog.name, rtl_win, emu_win, k - len(recent), k)
            pytest.fail(f"{prog.name}: instruction {k} differs\n  RTL {_fmt(a)}\n  emu {_fmt(b)}\n  (see {path})")
        recent.append((a, b))
        k += 1
    n_rtl += sum(1 for _ in rtl)
    n_emu += sum(1 for _ in emu)
    assert n_rtl, f"{prog.name}: the RTL trace has no instructions"
    assert abs(n_rtl - n_emu) <= 2, f"{prog.name}: RTL {n_rtl} vs emulator {n_emu} instructions"

    end = prog.cycles - UART_FRAME
    rtl_tx = [x for x in _decode_uart(rtl_uart, prog.cycles) if x[1] < end]
    emu_tx = [x for x in soc.uart_tx_log() if x[1] < end]
    assert rtl_tx == emu_tx, f"{prog.name}: UART TX differs\n  RTL {rtl_tx[:20]}\n  emu {emu_tx[:20]}"
