# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
MicroPython's Zephyr port (make build-micropython, board vux9k) on the emulator's real
profile. Like the demos it is linked into the PSRAM (the image at 0x8000_0000; its RAM from
0x8040_0000). Loaded into the PSRAM directly (Soc.load_app(image, "riscv", XMEM_BASE)): the
banner, then lines typed at the REPL (arbitrary-precision integers, the time module), and
Ctrl-C stopping a running loop. And once as the board starts it, from the card: the second
stage's `run VUX9K/APPS/MPY.BIN` (make apps).

As in test_zephyr_bc.py, the cycles, retired instructions and the caches' hit rates to the
first prompt, for the second stage's load and for each line are recorded and printed by
test_record_measurements: the emulator's counts are the RTL's (make sim-lockstep).
"""

import os

import pytest
from s1_env import STAGE2_BASE, app, run_from_card
from vux9k import CLK_HZ, REPO_ROOT, XMEM_BASE, Meter, hit_rates, new_soc, send_paced, wait_for

MICROPY_BIN = os.path.join(REPO_ROOT, "build", "micropython", "zephyr", "zephyr.bin")
BANNER = "MicroPython v1.29.0"
PROMPT = b">>> "
MORE = b"... "
# The board's flash (vux9k.dts): 4 MB of the PSRAM from XMEM_BASE; the second stage's `run`
# takes an image up to its own window
FLASH_BYTES = 4 * 1024 * 1024
# Upper bounds, not measurements (test_record_measurements prints those); most of the cycles
# are the PSRAM's waits, which the emulator adds in one step, so a bound costs little when hit
BOOT_CYCLES = 2_000_000_000
LINE_CYCLES = 500_000_000
# The second stage loads ~200 cycles a byte (s1_env.RUN_LOAD_CYCLES): ~35M for ~170 KB
RUN_LOAD_CYCLES = 100_000_000

MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}


@pytest.fixture(scope="module")
def mpy_bin():
    if not os.path.exists(MICROPY_BIN):
        pytest.skip(f"{MICROPY_BIN} missing (make build-micropython; needs Zephyr)")
    with open(MICROPY_BIN, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def mpy(mpy_bin):
    soc = new_soc()
    soc.load_app(mpy_bin, "riscv", XMEM_BASE)
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)
    meter.done("boot: start to the first >>> (load_app)")
    return soc, boot


def repl(soc, line: str, until: bytes = PROMPT) -> str:
    """One line typed at the REPL; what it prints up to the next prompt (`until`), the
    line's echo included. Measured from the first byte sent."""
    start = len(soc.uart_received())
    meter = Meter(soc, MEASURED)
    send_paced(soc, line.encode() + b"\r")  # Enter, as a terminal sends it
    out = wait_for(soc, until, timeout_cycles=LINE_CYCLES, start=start)
    meter.done(f"line: {line}")
    return out


def test_fits_the_board_and_the_run_window(mpy_bin):
    assert len(mpy_bin) <= min(FLASH_BYTES, STAGE2_BASE - XMEM_BASE)


def test_banner(mpy):
    assert BANNER in mpy[1], mpy[1]


def test_repl_expression(mpy):
    assert "\r\n3\r\n" in repl(mpy[0], "1+2")


def test_repl_big_int(mpy):
    assert "1267650600228229401496703205376" in repl(mpy[0], "2**100")


def test_repl_function(mpy):
    repl(mpy[0], "def sq(x): return x * x", until=MORE)
    repl(mpy[0], "")
    assert "\r\n144\r\n" in repl(mpy[0], "sq(12)")


def test_repl_exception(mpy):
    assert "ZeroDivisionError" in repl(mpy[0], "1 // 0")


def test_repl_time(mpy):
    """time.sleep_ms(100) on the kernel's clock (the SoC timer, 1000 ticks/s): k_poll for 100
    ms, a tick and rounding over it. The interpreter's own time around it is measured first
    with the same line less the sleep (a few ms: a few thousand instructions at ~10 cycles
    each from the PSRAM) and taken off; 100 ticks/s would read 110 or more."""
    soc = mpy[0]
    repl(soc, "import time")

    def elapsed(sleep: str) -> int:
        out = repl(soc, f"t = time.ticks_ms(); {sleep}time.ticks_diff(time.ticks_ms(), t)")
        return int(out.splitlines()[-2])  # the value's line, before the next prompt's

    overhead = elapsed("time.sleep_ms(0); ")
    slept = elapsed("time.sleep_ms(100); ")
    assert 100 <= slept - overhead <= 102, (slept, overhead)


def test_ctrl_c_stops_a_loop(mpy):
    """Ctrl-C in the RX interrupt (the UART console's hook, prj.conf) stops a running loop."""
    soc = mpy[0]
    repl(soc, "while True: pass", until=MORE)
    send_paced(soc, b"\r")
    soc.run(CLK_HZ // 10)  # the loop runs for 100 ms
    start = len(soc.uart_received())
    send_paced(soc, b"\x03")
    out = wait_for(soc, PROMPT, timeout_cycles=LINE_CYCLES, start=start)
    assert "KeyboardInterrupt" in out, out
    assert "\r\n7\r\n" in repl(soc, "3+4")  # and the REPL goes on


def test_runs_from_the_card(mpy_bin):
    """As on the board: the second stage's `run VUX9K/APPS/MPY.BIN` loads it into the PSRAM
    and starts it there; it prints the banner and the prompt."""
    soc, mark = run_from_card("MPY.BIN", app("MPY.BIN"), load_cycles=RUN_LOAD_CYCLES)
    MEASURED["power-on to the start (both stages, run's load)"] = (soc.cycle, soc.instret, soc.cache_stats)
    run_line = soc.uart_received()[:mark].decode(errors="replace")
    assert "PSRAM, starting at 0x80000000" in run_line, run_line[-400:]
    assert soc.xmem_word(XMEM_BASE) == int.from_bytes(mpy_bin[:4], "little")
    meter = Meter(soc, MEASURED)
    out = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES, start=mark)
    meter.done("boot: start to the first >>> (second stage's run)")
    assert BANNER in out
    assert "VUX9K Stage" not in out  # nothing of the first or second stage after the start


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED:
        pytest.skip("nothing measured (MicroPython not built)")
    with capsys.disabled():
        print(f"\nMicroPython on the emulator from the PSRAM (18 MHz: {CLK_HZ:,} cycles a second)")
        for label, (cycles, instret, stats) in MEASURED.items():
            cpi = cycles / max(instret, 1)
            print(f"  {label:<56} {cycles:>15,} cycles {instret:>13,} instret  CPI {cpi:.1f}  {hit_rates(stats)}")
            record_property(label, f"{cycles} cycles, {instret} instret, {hit_rates(stats)}")
