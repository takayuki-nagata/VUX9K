# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
bc_clone_rs on Zephyr (make build-zephyr, board vux9k) on the emulator's real profile.
Like the demos it is linked into the PSRAM (the image at 0x8000_0000, ~260 KB; its RAM
from 0x8040_0000), where a fetch or load that misses the caches waits ~35 cycles and every
store ~34. Loaded into the PSRAM directly (Soc.load_app(image, "riscv", XMEM_BASE)): the
self-tests, then the interactive REPL. And once as the board starts it, from the card: the
second stage's `run VUX9K/APPS/BC.BIN` (make apps) loads it and jumps to it.

The emulator follows the RTL cycle for cycle (make sim-lockstep), so its counts are the
RTL's (the board's wall-clock times in test_hardware.py's test 14 agree): the tests record
the cycles, retired instructions and the caches' hit rates to bc's first prompt, for the
second stage's load and for each REPL line, and test_record_measurements prints them
(pytest output and junit properties), to compare speed work against.
(Ported from sim/emulator/test_soc_bc.py.)
"""

import os

import pytest
from s1_env import STAGE2_BASE, app, run_from_card
from vux9k import REPO_ROOT, XMEM_BASE, new_soc, send_paced, wait_for

ZEPHYR_BIN = os.path.join(REPO_ROOT, "build", "zephyr", "zephyr", "zephyr.bin")
SELF_TESTS = [
    "Basic Arithmetic & Precedence",
    "Scale Division",
    "BigInt Power (2^100)",
    "Recursive Factorial f(20)",
    "Transcendental Pi: 4 * a(1)",
    "Transcendental Exp: e(1)",
    "Transcendental Log: l(2.718281828459045)",
    "Base Conversion (Hex to Binary)",
    "Arrays and Dynamic Auto Scoping",
    "Streaming Callback Test: 2^16 = 65536",
    "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!",
]
# The board's flash (vux9k.dts): 4 MB of the PSRAM from XMEM_BASE; the second stage's `run`
# takes an image up to its own window
FLASH_BYTES = 4 * 1024 * 1024
# Upper bounds, not measurements (test_record_measurements prints those): from I-RAM, on an
# emulator profile with bigger memories (since removed), the self-tests reached the prompt
# within 2,000M cycles and a line within 200M; from the PSRAM (+35 cycles a fetch or load,
# +34 a store) several times that. A bound costs little wall time when it is hit: most of
# the cycles are waits, which the emulator adds in one step, not one by one.
BOOT_CYCLES = 20_000_000_000
LINE_CYCLES = 2_000_000_000
# The second stage loads ~200 cycles a byte (s1_env.RUN_LOAD_CYCLES): ~52M for bc
BC_RUN_LOAD_CYCLES = 200_000_000

# label -> (cycles, retired instructions, the caches' counts), in the order measured
MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}


class Meter:
    """Cycles, minstret and the caches' counts from now on, recorded under a label by done()."""

    def __init__(self, soc):
        self.soc = soc
        self.cycle, self.instret, self.stats = soc.cycle, soc.instret, soc.cache_stats

    def done(self, label: str) -> None:
        now = self.soc.cache_stats
        stats = {k: now[k] - self.stats[k] for k in now}
        MEASURED[label] = (self.soc.cycle - self.cycle, self.soc.instret - self.instret, stats)


def hit_rates(stats: dict[str, int]) -> str:
    """The fetches' and loads' hit rates in the window, and its stores."""

    def rate(hits: int, misses: int) -> str:
        return f"{100 * hits / max(hits + misses, 1):5.1f}%"

    i = rate(stats["fetch_hits"], stats["fetch_misses"])
    d = rate(stats["load_hits"], stats["load_misses"])
    return f"I {i} D {d} stores {stats['stores']:,}"


@pytest.fixture(scope="module")
def bc_bin():
    if not os.path.exists(ZEPHYR_BIN):
        pytest.skip(f"{ZEPHYR_BIN} missing (make build-zephyr; needs Zephyr)")
    with open(ZEPHYR_BIN, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def bc(bc_bin):
    soc = new_soc()
    soc.load_app(bc_bin, "riscv", XMEM_BASE)
    meter = Meter(soc)
    boot = wait_for(soc, b"bc> ", timeout_cycles=BOOT_CYCLES)
    meter.done("boot: start to the first bc> (load_app)")
    return soc, boot


def repl(soc, line: str) -> str:
    """One line typed at bc's prompt; its output up to the next prompt. Measured from the
    first byte sent (the line's own UART time, ~1,560 cycles a byte, included)."""
    start = len(soc.uart_received())
    meter = Meter(soc)
    send_paced(soc, line.encode() + b"\r")  # Enter, as a terminal sends it
    out = wait_for(soc, b"bc> ", timeout_cycles=LINE_CYCLES, start=start)
    meter.done(f"line: {line}")
    return out


def test_fits_the_board_and_the_run_window(bc_bin):
    assert len(bc_bin) <= min(FLASH_BYTES, STAGE2_BASE - XMEM_BASE)


@pytest.mark.parametrize("line", SELF_TESTS)
def test_self_test(bc, line):
    _, boot = bc
    assert line in boot


def test_repl_power(bc):
    assert "4294967296" in repl(bc[0], "2^32")


def test_repl_function(bc):
    repl(bc[0], "define cube(x) { return (x^3); }")
    assert "125" in repl(bc[0], "cube(5)")


def test_repl_scale_division(bc):
    assert "3.142857" in repl(bc[0], "scale = 6; 22 / 7")


def test_runs_from_the_card(bc_bin):
    """As on the board: the second stage's `run VUX9K/APPS/BC.BIN` loads it into the PSRAM
    and starts it there; the self-tests pass and bc prompts."""
    data = app("BC.BIN")
    soc, mark = run_from_card("BC.BIN", data, load_cycles=BC_RUN_LOAD_CYCLES)
    MEASURED["power-on to the start (both stages, run's load)"] = (soc.cycle, soc.instret, soc.cache_stats)
    run_line = soc.uart_received()[:mark].decode(errors="replace")
    assert "PSRAM, starting at 0x80000000" in run_line, run_line[-400:]
    assert soc.xmem_word(XMEM_BASE) == int.from_bytes(bc_bin[:4], "little")
    meter = Meter(soc)
    out = wait_for(soc, b"bc> ", timeout_cycles=BOOT_CYCLES, start=mark)
    meter.done("boot: start to the first bc> (second stage's run)")
    assert "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!" in out
    assert "VUX9K Stage" not in out  # nothing of the first or second stage after the start


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED:
        pytest.skip("nothing measured (bc not built)")
    with capsys.disabled():
        print(f"\nbc on the emulator from the PSRAM (18 MHz: {18_000_000:,} cycles a second)")
        for label, (cycles, instret, stats) in MEASURED.items():
            cpi = cycles / max(instret, 1)
            print(f"  {label:<56} {cycles:>15,} cycles {instret:>13,} instret  CPI {cpi:.1f}  {hit_rates(stats)}")
            record_property(label, f"{cycles} cycles, {instret} instret, {hit_rates(stats)}")
