# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
noMMU Linux (make linux: linux_workspace/, the kernel with buildroot's busybox initramfs built
in) on the emulator's real profile, the SoC as synthesized (docs/agents/linux.md), booted two
ways: the kernel Image and the device tree loaded into the PSRAM (Soc.load_linux), and the
same files on a FAT32 card (vux_tool's mkfat, `\\VUX9K\\LINUX\\Image` and `vux9k.dtb`) that the
second stage, booted by the first from the card, loads and starts with `linux`, as on the
board. Each boots to busybox's shell on the console, then commands are typed at it
(sim/integration/linux_checks.py, which the board's scripts/hw_linux.py checks too).

On each boot, after the boot's commands: MicroPython from the card (linux_checks.MPY_COMMANDS:
its modules, which the login shell's profile extracted from the card's PYLIB.TAR, bc_clone_py's
self-test, scripts on the card, an LED, the heap's growth; then its REPL); after the other
commands: the card's sample scripts, vi driven by its keys
(linux_checks.VI_SESSIONS; the file then read from the card image) and, last, a paste at the
shell, back to back (its dropped bytes recorded) and as vux_tool's monitor paces it (none).

A third boot (load_linux) has a card that waits before every block it reads, as a real card
does (test_card_with_read_waits). Three more (load_linux) boot cards without PYLIB.TAR and
with it, twice: the profile extracts it at the first boot of a card and leaves it at the next
(test_pylib_extraction).

The cycles, retired instructions and the caches' hit rates to the prompt (for the card: the
second stage's load, `linux` typed to its [LINUX] line, and the kernel's boot from there) and
for each command are printed by test_record_measurements. The board's test is scripts/hw_linux.py
(make test-hw-linux), which test_board_script_on_the_emulator runs here against the CLI.
"""

import json
import math
import os
import random
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integration"))
import linux_checks  # noqa: E402
import s1_env  # noqa: E402
import test_fat_images  # noqa: E402
from vux9k import (  # noqa: E402
    CLK_HZ,
    REPO_ROOT,
    UART_BIT,
    Meter,
    Soc,
    hit_rates,
    ms,
    new_soc,
    press_button,
    send_paced,
    vux_tool,
    wait_for,
)

LINUX_DIR = os.path.join(REPO_ROOT, "build", "linux")
IMAGE = os.path.join(LINUX_DIR, "arch", "riscv", "boot", "Image")
DTB = os.path.join(LINUX_DIR, "vux9k.dtb")
# make linux-dist's tree of the same build, which scripts/hw_linux.py takes
LINUX_DIST = os.path.join(REPO_ROOT, "build", "linux-dist")
EMU = os.environ.get("VUX9K_EMU_BIN") or os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")
PROMPT = linux_checks.PROMPT.encode()
# Upper bounds, not measurements (test_record_measurements prints those): the boot to the
# prompt took ~224M cycles and a command that executes busybox ~15M (2026-11-01); the second
# stage reads the 3.2 MB Image at ~70 cycles a byte, ~230M (2026-11-07)
BOOT_CYCLES = 10_000_000_000
COMMAND_CYCLES = 2_000_000_000
LOAD_CYCLES = 3_000_000_000
# The card: 8 MB, room for the Image, the device tree, the second stage and the programs for
# the card
CARD_SECTORS = 16384
# Wall-clock seconds for scripts/hw_linux.py on the emulator's CLI (with MicroPython's ~0.5 MB
# put and its commands)
BOARD_SCRIPT_TIMEOUT_S = 1800
# test_board_script_with_emulator_stalls: how long the emulator stays stopped at most on a
# write while hw_linux.py types a line (8 bytes every 20 ms: a line of up to ~80 bytes reaches
# the UART in one go), at most how many times, its own bound (the commands' and probes' ~3,300
# bytes, a stop on every chunk the line's end isn't in: ~350 x 0.2 s more), and the line of
# hw_linux.py's output at which the stops end (before vi: its keys and the paste go out by the
# host's clock, not by echo)
STALL_HOLD_S = 0.2
STALL_MAX = 5000
BOARD_SCRIPT_STALLED_TIMEOUT_S = BOARD_SCRIPT_TIMEOUT_S + 300
STALL_UNTIL = "[INFO] the emulator: vi's split steps"

MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}
# The memory table of each boot path (linux_checks.memory), read right after the boot
MEMORY: dict[str, dict[str, str]] = {}
# The CPU init's sysmon took over IDLE_S seconds of an idle shell, per boot path (%)
SYSMON_CPU: dict[str, float] = {}
IDLE_S = 10
# What test_page_cache_reclaim saw, per boot path
RECLAIM: dict[str, str] = {}
# What test_paste saw, per boot path and way of sending
PASTE: dict[str, str] = {}
# What test_pylib_extraction saw: each boot's cycles to the prompt, by its label
PYLIB_BOOTS: dict[str, int] = {}
# sysmon's reclaim (linux_workspace/userland/sysmon/src/lib.rs, BUSYBOX_ORDER, RECLAIM_EVERY):
# the order of the initramfs' busybox's exec (the board's test waits for it too), and every how
# many intervals it looks
BUSYBOX_ORDER = linux_checks.BUSYBOX_ORDER
RECLAIM_EVERY = linux_checks.SYSMON_RECLAIM_EVERY
# 2 x 1 MB from the card, other blocks than the commands' reads before (the spike, 2026-11-15:
# the largest free block order 5 after them, Cached ~1,100 kB)
RECLAIM_READS = tuple(f"dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=256 skip={s}" for s in (512, 768))
# The processes that hold the free blocks of BUSYBOX_ORDER and up that the reads left (each a
# copy of busybox, one such block), at most
RECLAIM_HOLDERS = 12
# test_reclaim_for_mpy's MPY, and the MPYs that hold the free blocks of MPY_ORDER, at most
MPY_PROBE = 'MPY -c "print(6 * 7)"'
MPY_HOLDER = 'MPY -c "import time; time.sleep(600)"'
MPY_HOLDERS = 4
# test_reclaim_for_mpy's reads, 1 MB each, of blocks no test read before (RECLAIM_READS' cache
# may still be there), until no free block of MPY_ORDER is left (the boot leaves an order-7 and
# an order-8 one)
MPY_RECLAIM_READS = tuple(f"dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=256 skip={s}" for s in (1024, 1280, 1536))
# The kernel's line for sysmon's write of 4 at its start, the one drop_caches line it prints
DROP_CACHES_QUIET = "): drop_caches: 4"
# How long a test waits for sysmon to give back a free block of BUSYBOX_ORDER (its interval is
# 1 s), in seconds
RECLAIM_WAIT_S = linux_checks.RECLAIM_WAIT_S
# The LEDs sysmon's bar has (led1-led4)
BAR = 0b011110
# The card of test_card_with_read_waits: FF bytes before each data block's token beyond the one
# the card always sends (sdcard::Faults::read_waits, one per block in turn), as a real card's
# N(AC). mmc_spi reads 16 bytes per SPI message while it looks for the token
# (linux_workspace/kernel/patches/0004-mmc-spi-token-search.patch). For a single block
# (CMD17) its first read starts at that one FF, so the token is that read's byte wait + 1;
# for a multiple block read (CMD18) mmc_spi's early_status byte has taken the FF, so it is
# byte wait, one byte earlier. As counted for CMD17: before its last (13), its last (14), the
# next read's first (15), the same at the second read's end (29-31) and the third's (46), and
# early in the first (0, 1, 7).
READ_WAITS = (13, 14, 15, 0, 1, 29, 30, 31, 46, 7)
# On that card, past the partition: REF_SECTORS of random data a dd copies through Linux to
# as many sectors after them, which the test compares with the original
REF_SECTORS = 512


@dataclass
class Booted:
    soc: Soc
    boot: str  # the console's output to the first prompt
    how: str  # the boot path, for the measurements' labels


def card_programs() -> dict[str, bytes]:
    """make linux-dist's programs for the card (VUX9K/LINUX/BIN/) and MicroPython's modules'
    archive (linux_checks.PYLIB_TAR) with its version (PYLIB_VER), as its MANIFEST.json lists
    them."""
    try:
        with open(os.path.join(LINUX_DIST, "MANIFEST.json")) as f:
            listed = json.load(f)["card_files"]
    except FileNotFoundError:
        return {}
    progs = {}
    for rel in listed:
        if rel.startswith("VUX9K/LINUX/BIN/") or rel in (linux_checks.PYLIB_TAR, linux_checks.PYLIB_VER):
            with open(os.path.join(LINUX_DIST, rel), "rb") as f:
                progs[rel] = f.read()
    return progs


def card(image: bytes, dtb: bytes, without: tuple[str, ...] = ()) -> bytes:
    """The card both boot paths have in the socket (the kernel mounts it): the second stage,
    the Image and device tree where its `linux` looks for them, and the programs for the card
    (but those in without)."""
    files = {"VUX9K/STAGE2.BIN": s1_env.stage2(), "VUX9K/LINUX/Image": image, "VUX9K/LINUX/vux9k.dtb": dtb}
    progs = {k: v for k, v in card_programs().items() if k not in without}
    return vux_tool.build_fat32_image(files | progs, part_sectors=CARD_SECTORS)


def boot_direct(image: bytes, dtb: bytes, sd: bytes | None = None, how: str = "load_linux", **faults) -> Booted:
    """sd: the card (card()'s by default); faults: Soc.sd_insert's."""
    soc = new_soc()
    soc.sd_insert(card(image, dtb) if sd is None else sd, **faults)
    soc.load_linux(image, dtb)
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)
    meter.done(f"{how}: start to the first prompt")
    return Booted(soc, boot, how)


def boot_from_card(image: bytes, dtb: bytes) -> Booted:
    soc, _ = s1_env.boot_s2(image=card(image, dtb))
    soc.run(50 * UART_BIT)
    mark = len(soc.uart_received())
    meter = Meter(soc, MEASURED)
    soc.uart_send(b"linux\r")
    out = wait_for(soc, b"[LINUX] ", timeout_cycles=LOAD_CYCLES, start=mark)
    meter.done("stage2: linux typed to [LINUX] (the load)")
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES, start=mark + len(out))
    meter.done("stage2: [LINUX] to the first prompt")
    line = boot.splitlines()[0]
    assert line == f"Image {len(image)} B, DTB {len(dtb)} B, starting at 0x80000000", line
    return Booted(soc, boot, "stage2")


def linux_files() -> tuple[bytes, bytes]:
    """make linux's Image and device tree; skips without them or VUX9K_LINUX_TEST."""
    # Minutes of emulation: only make test-linux-emu runs it, not test-emu (pytest sim/emu)
    if not os.environ.get("VUX9K_LINUX_TEST"):
        pytest.skip("make test-linux-emu runs it (VUX9K_LINUX_TEST=1)")
    for path in (IMAGE, DTB):
        if not os.path.exists(path):
            pytest.skip(f"{path} missing (make linux)")
    with open(IMAGE, "rb") as image, open(DTB, "rb") as dtb:
        return image.read(), dtb.read()


@pytest.fixture(scope="module", params=[boot_direct, boot_from_card], ids=["load_linux", "stage2"])
def linux(request) -> Booted:
    return request.param(*linux_files())


def command(linux: Booted, line: str) -> str:
    """One command typed at the shell; what it prints up to the next prompt, its echo
    included. Measured from the first byte sent."""
    soc = linux.soc
    start = len(soc.uart_received())
    meter = Meter(soc, MEASURED)
    send_paced(soc, line.encode() + b"\n")
    out = wait_for(soc, PROMPT, timeout_cycles=COMMAND_CYCLES, start=start + len(line))
    meter.done(f"{linux.how}: command: {line}")
    return out


def test_boot_banner(linux):
    assert not linux_checks.missing(linux.boot, linux_checks.BOOT_LINES), linux.boot[-2000:]


def build_sizes() -> dict[str, int]:
    """The Image's, the initramfs' and busybox's sizes in make linux-dist's MANIFEST.json."""
    try:
        with open(os.path.join(LINUX_DIST, "MANIFEST.json")) as f:
            return json.load(f).get("sizes", {})
    except FileNotFoundError:
        return {}


def test_memory(linux):
    """The memory the userland has after the boot, read with the shell's builtins (no exec):
    recorded for test_record_measurements, not held to a budget here."""
    meminfo, buddyinfo = (command(linux, line) for line in linux_checks.MEM_PROBES)
    MEMORY[linux.how] = linux_checks.memory(meminfo, buddyinfo, build_sizes())


@pytest.mark.parametrize("line", list(linux_checks.BASE_COMMANDS))
def test_command(linux, line):
    out = command(linux, line)
    assert not linux_checks.missing(out, linux_checks.BASE_COMMANDS[line]), out


EXEC_FAILED = ("page allocation failure", "Unable to allocate RAM")
# 1.5 MB read: more page cache than the free memory's largest blocks
READS = 6


@pytest.mark.parametrize("line", list(linux_checks.MPY_COMMANDS))
def test_mpy_command(linux, line):
    """MicroPython on the card (linux_checks.MPY_COMMANDS; docs/agents/linux.md,
    "MicroPython"), right after the boot's commands: each exec of MPY waits for a free block of
    MPY_ORDER first (wait_large), which sysmon's reclaim gives back once the card's reads and
    MPY's own execs have broken the boot's ones. The LED script lights MPY_LED on the GPIO
    register."""
    if linux_checks.runs_mpy(line):
        assert wait_large(linux, linux_checks.MPY_ORDER), f"no free block of order {linux_checks.MPY_ORDER} for MPY"
    out = command(linux, line)
    assert not any(e in out for e in EXEC_FAILED), out[:600]
    assert not linux_checks.missing(out, linux_checks.MPY_COMMANDS[line]), out[-1500:]
    assert not linux_checks.problems(line, out), out[-1500:]
    if line == f"MPY {linux_checks.MPY_LED_PY}":
        n = int(linux_checks.MPY_LED.removeprefix("led"))
        assert linux.soc.leds & (1 << n), hex(linux.soc.leds)


def test_mpy_repl(linux):
    """MicroPython's REPL: MPY alone, its banner and prompt, 1+1, and Ctrl-D back to the shell;
    on a failure Ctrl-C (and Ctrl-D once in the REPL) for the tests after this one."""
    soc = linux.soc
    assert wait_large(linux, linux_checks.MPY_ORDER), f"no free block of order {linux_checks.MPY_ORDER} for MPY"
    repl_prompt = linux_checks.MPY_REPL_PROMPT.encode()
    in_repl = False
    try:
        start = len(soc.uart_received())
        meter = Meter(soc, MEASURED)
        send_paced(soc, f"{linux_checks.MPY_REPL}\n".encode())
        banner = wait_for(soc, repl_prompt, timeout_cycles=COMMAND_CYCLES, start=start)
        meter.done(f"{linux.how}: MPY to the REPL's prompt")
        in_repl = True
        assert linux_checks.MPY_VERSION in banner, banner[-1500:]
        start = len(soc.uart_received())
        send_paced(soc, f"{linux_checks.MPY_REPL_LINE}\r".encode())
        out = wait_for(soc, repl_prompt, timeout_cycles=COMMAND_CYCLES, start=start + len(linux_checks.MPY_REPL_LINE))
        assert linux_checks.MPY_REPL_OUT in [o.strip() for o in out.splitlines()], out
        start = len(soc.uart_received())
        send_paced(soc, linux_checks.MPY_REPL_EXIT.encode())
        wait_for(soc, PROMPT, timeout_cycles=COMMAND_CYCLES, start=start)
    except Exception:
        start = len(soc.uart_received())
        send_paced(soc, b"\x03" + (linux_checks.MPY_REPL_EXIT.encode() if in_repl else b""))
        wait_for(soc, PROMPT, timeout_cycles=COMMAND_CYCLES, start=start)
        raise


def test_exec_after_card_reads(linux):
    """Busybox's exec after each of READS more 256 KB reads from the card (other blocks than
    BASE_COMMANDS' dd): the page cache they leave breaks the free memory's large blocks (noMMU
    has no compaction), and busybox must still find a block for its copy (docs/agents/linux.md,
    "Memory budget")."""
    for k in range(1, READS + 1):
        command(linux, f"dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=64 skip={64 * k}")
        out = command(linux, "ls /")
        assert not any(e in out for e in EXEC_FAILED), f"after read {k}: {out[:300]}"


@pytest.mark.parametrize("line", list(linux_checks.LED_COMMANDS))
def test_led_command(linux, line):
    out = command(linux, line)
    assert not linux_checks.missing(out, linux_checks.LED_COMMANDS[line]), out


def test_leds(linux):
    """The LEDs on the GPIO register (soc.leds: bit n = LED n) from the LED class: a value
    through brightness once the LED's trigger is gone, the timer trigger blinking it, the
    default triggers given back."""
    soc = linux.soc
    command(linux, "echo none > /sys/class/leds/led5/trigger")
    command(linux, "echo 1 > /sys/class/leds/led5/brightness")
    assert soc.leds & 0b100000, hex(soc.leds)
    command(linux, "echo 0 > /sys/class/leds/led5/brightness")
    assert soc.leds & 0b100000 == 0, hex(soc.leds)
    command(linux, "echo none > /sys/class/leds/led0/trigger")
    command(linux, "echo 0 > /sys/class/leds/led0/brightness")
    soc.run(ms(2000))
    assert soc.leds & 0b100001 == 0, "led0 or led5 changed without a trigger"
    # The timer trigger: 500 ms on, 500 ms off
    command(linux, "echo timer > /sys/class/leds/led0/trigger")
    seen = set()
    for _ in range(12):
        soc.run(ms(200))
        seen.add(soc.leds & 1)
    assert seen == {0, 1}, seen
    command(linux, "echo heartbeat > /sys/class/leds/led0/trigger")
    command(linux, "echo mmc0 > /sys/class/leds/led5/trigger")
    assert "[mmc0]" in command(linux, "cat /sys/class/leds/led5/trigger")


def free_large(linux: Booted, order: int = BUSYBOX_ORDER) -> int:
    """The free blocks of order (BUSYBOX_ORDER) and above, read with the shell's builtins."""
    orders = linux_checks.parse_buddyinfo(command(linux, linux_checks.MEM_PROBES[1]))
    assert orders, "no /proc/buddyinfo"
    return sum(orders[order:])


def wait_large(linux: Booted, order: int = BUSYBOX_ORDER) -> int:
    """Waits up to RECLAIM_WAIT_S of the guest's time for a free block of order (sysmon's
    reclaim gives one back): BUSYBOX_ORDER, which the next exec of busybox needs, or
    linux_checks.MPY_ORDER, MPY's; the count, 0 if none came."""
    for _ in range(RECLAIM_WAIT_S):
        n = free_large(linux, order)
        if n:
            return n
        linux.soc.run(ms(1000))
    return free_large(linux, order)


@pytest.mark.parametrize("line", list(linux_checks.TOOL_COMMANDS))
def test_tool_command(linux, line):
    """The text tools, the card's busyboxes (docs/agents/linux.md, "Text tools and vi"), after
    the card reads above: each exec finds its block, once sysmon has given back one of order 6
    (bbawk's and busybox's; wait_large)."""
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} before {line!r}"
    out = command(linux, line)
    assert not any(e in out for e in EXEC_FAILED), out[:600]
    assert not linux_checks.missing(out, linux_checks.TOOL_COMMANDS[line]), out


@pytest.mark.parametrize("line", list(linux_checks.PIPE_COMMANDS))
def test_pipe_command(linux, line):
    """The text tools' pipes (on test_tool_command's files), whose processes each hold a block
    at once, the { } one busybox's too (docs/agents/linux.md, "Text tools and vi")."""
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} before {line!r}"
    out = command(linux, line)
    assert not any(e in out for e in EXEC_FAILED), out[:600]
    # The lines without uniq -c's alignment
    got = [" ".join(o.split()) for o in out.splitlines()[1:]]
    assert all(w in got for w in linux_checks.PIPE_COMMANDS[line]), out


def wait_console(linux: Booted, token: str, start: int) -> str:
    """Runs until the console shows token after index start (a line init or a background
    process prints, not the shell's prompt)."""
    return wait_for(linux.soc, token.encode(), timeout_cycles=COMMAND_CYCLES, start=start)


def background(linux: Booted, line: str) -> int:
    """A command started in the background at the shell; its process id ($!)."""
    out = command(linux, f"{line} & echo bg=$!")
    m = re.search(r"bg=(\d+)", out)
    assert m, out
    return int(m.group(1))


def sysmon_pid(linux: Booted) -> int:
    out = command(linux, "ps")
    found = linux_checks.pids(out, linux_checks.SYSMON)
    assert len(found) == 1, (found, out)
    return found[0]


def test_sysmon(linux):
    """init's system monitor on the GPIO register's led1-led4 (soc.leds): its CPU over an idle
    shell, its bar under a busy loop of the shell's own (no exec), the button stepping its
    mode, started again after it is killed, then stopped (its LEDs dark) by SIGTERM and left
    so. Its lines are on the console."""
    soc = linux.soc
    pid = sysmon_pid(linux)
    ticks = linux_checks.cpu_ticks(command(linux, f"cat /proc/{pid}/stat"))
    soc.run(IDLE_S * CLK_HZ)
    ticks = linux_checks.cpu_ticks(command(linux, f"cat /proc/{pid}/stat")) - ticks
    SYSMON_CPU[linux.how] = ticks / IDLE_S  # 100 ticks a second (HZ 100): % of the CPU
    idle = soc.leds & BAR
    loop = background(linux, "while :; do :; done")
    soc.run(ms(3000))
    busy = soc.leds & BAR
    command(linux, f"kill {loop}")
    assert busy == BAR and idle != BAR, (hex(idle), hex(busy))

    # Held and released for longer than sysmon's 200 ms between reads of the button: a shorter
    # release between two presses is one press to it
    mark = len(soc.uart_received())
    press_button(soc, hold_ms=500, after_ms=500)
    wait_console(linux, "sysmon: mode mem", mark)
    press_button(soc, hold_ms=500, after_ms=500)
    wait_console(linux, "sysmon: mode load", mark)

    # Killed by a signal: init starts it again, a new process
    mark = len(soc.uart_received())
    command(linux, f"kill -SEGV {pid}")
    wait_console(linux, f"init: {linux_checks.SYSMON_PATH} killed by signal 11", mark)
    wait_console(linux, "sysmon: mode cpu", mark)
    pid2 = sysmon_pid(linux)
    assert pid2 != pid
    # Stopped: its LEDs off, and init leaves it stopped
    mark = len(soc.uart_received())
    command(linux, f"kill {pid2}")
    wait_console(linux, f"init: {linux_checks.SYSMON_PATH} stopped", mark)
    assert soc.leds & BAR == 0, hex(soc.leds)
    soc.run(ms(6000))
    assert linux_checks.pids(command(linux, "ps"), linux_checks.SYSMON) == []
    # One again from the shell, for the tests after this one: its reclaim of the page cache
    background(linux, linux_checks.SYSMON)


def test_page_cache_reclaim(linux):
    """sysmon drops the page cache when the card's reads have left no free block of order 7
    (MPY's exec; noMMU has no compaction: docs/agents/linux.md, "Memory budget"), and gives
    back one of order 6 (busybox's) whatever it brings back of order 7. With no
    sysmon running (test_sysmon's last one stopped), 2 MB read from the card, and the free
    blocks of order 6 and up that are left taken by sleeping busyboxes (each dd and each sleep
    is an exec of busybox, which needs such a block itself: the reads can't leave none), none
    is free. A sysmon with -r 0 (no reclaim) leaves it so for two of its looks; one with the
    reclaim says it dropped the page cache, and a block is free again while the sleepers still
    hold theirs. The kernel printed one drop_caches line in all, init's sysmon's 4 at its
    start."""
    soc = linux.soc
    mark = len(soc.uart_received())
    command(linux, f"kill {sysmon_pid(linux)}")
    wait_console(linux, "sysmon: stopped", mark)
    holders: list[int] = []
    # From here on a failure still leaves the sleepers killed and a sysmon running, for the
    # tests after this one
    try:
        reads = 0
        for line in RECLAIM_READS:
            if not free_large(linux):
                break
            assert "256+0 records in" in command(linux, line)
            reads += 1
        while free_large(linux) and len(holders) < RECLAIM_HOLDERS:
            holders.append(background(linux, "sleep 600"))
        buddyinfo = command(linux, linux_checks.MEM_PROBES[1])
        seen = f"free blocks of order >= {BUSYBOX_ORDER}: {reads} reads, {len(holders)} sleepers"
        RECLAIM[linux.how] = seen
        assert sum(linux_checks.parse_buddyinfo(buddyinfo)[BUSYBOX_ORDER:]) == 0, f"{seen}: {buddyinfo}"
        # No reclaim: nothing comes back
        mark = len(soc.uart_received())
        background(linux, f"sysmon -r 0 -v -n {2 * RECLAIM_EVERY}")
        wait_console(linux, "sysmon: stopped", mark)
        without = free_large(linux)
        RECLAIM[linux.how] = seen = f"{seen}; free blocks after sysmon -r 0 {without}"
        assert without == 0, seen
        # The reclaim: a drop at its first look, and a block back
        mark = len(soc.uart_received())
        background(linux, f"sysmon -v -n {2 * RECLAIM_EVERY}")
        dropped = wait_console(linux, "sysmon: dropped the page cache", mark)
        wait_console(linux, "sysmon: stopped", mark)
        after = free_large(linux)
        RECLAIM[linux.how] = seen = f"{seen}, after sysmon's drop {after}"
        assert after > 0, f"{seen}: {dropped[-300:]}"
    finally:
        if holders:
            command(linux, "kill " + " ".join(str(p) for p in holders))
        # In place of init's
        background(linux, linux_checks.SYSMON)
    console = soc.uart_received().decode(errors="replace")
    lines = [ln for ln in console.splitlines() if "): drop_caches: " in ln]
    assert len(lines) == 1 and DROP_CACHES_QUIET in lines[0] and f"{linux_checks.SYSMON} (" in lines[0], lines
    assert "sysmon: cannot write /proc/sys/vm/drop_caches" not in console


def test_reclaim_for_mpy(linux):
    """sysmon keeps a free block of order 7 for MPY's exec (linux_checks.MPY_ORDER; docs/agents/
    linux.md, "Memory budget") after the card's reads. With no sysmon running, reads from the
    card (MPY_RECLAIM_READS) until none of order 7 is free; a sysmon with -r 0 leaves it so for
    two of its looks; one with the reclaim says it dropped the page cache for order 7, a block
    of order 7 is free again, and MPY runs in it. Then, with MPYs holding every free block of
    order 7, what one more MPY shows is recorded (test_record_measurements)."""
    soc = linux.soc
    order = linux_checks.MPY_ORDER
    mark = len(soc.uart_received())
    command(linux, f"kill {sysmon_pid(linux)}")
    wait_console(linux, "sysmon: stopped", mark)
    holders: list[int] = []
    try:
        reads = 0
        # Each dd is an exec of busybox: an order-7 block left is one of order 6 too
        for line in MPY_RECLAIM_READS:
            if not free_large(linux, order):
                break
            assert "256+0 records in" in command(linux, line)
            reads += 1
        buddyinfo = command(linux, linux_checks.MEM_PROBES[1])
        key = f"{linux.how}, MPY"
        RECLAIM[key] = seen = f"free blocks of order >= {order}: {reads} reads"
        assert sum(linux_checks.parse_buddyinfo(buddyinfo)[order:]) == 0, f"{seen}: {buddyinfo}"
        mark = len(soc.uart_received())
        background(linux, f"sysmon -r 0 -v -n {2 * RECLAIM_EVERY}")
        wait_console(linux, "sysmon: stopped", mark)
        without = free_large(linux, order)
        RECLAIM[key] = seen = f"{seen}; after sysmon -r 0 {without}"
        assert without == 0, seen
        mark = len(soc.uart_received())
        background(linux, f"sysmon -v -n {2 * RECLAIM_EVERY}")
        dropped = wait_console(linux, f"sysmon: dropped the page cache (no free block of order {order},", mark)
        wait_console(linux, "sysmon: stopped", mark)
        after = free_large(linux, order)
        RECLAIM[key] = seen = f"{seen}, after sysmon's drop {after}"
        assert after > 0, f"{seen}: {dropped[-300:]}"
        out = command(linux, MPY_PROBE)
        assert "42" in out and not any(e in out for e in EXEC_FAILED), out[-1500:]
        # What MPY shows without a block for it (docs/agents/linux.md, "MicroPython"): MPYs
        # sleeping in the background hold the free blocks of order 7 and up, each its own, then
        # one more. Recorded, not held to a text: the allocator may still find a block (pages
        # on its per-CPU lists, which buddyinfo doesn't count), and then it runs
        while free_large(linux, order) and len(holders) < MPY_HOLDERS:
            holders.append(background(linux, MPY_HOLDER))
            # Its exec copies MPY into its block after the shell has gone on
            soc.run(ms(2000))
        out = command(linux, MPY_PROBE)
        failed = any(e in out for e in EXEC_FAILED)
        assert failed or "42" in out, out[-1500:]
        shown = [ln.strip() for ln in out.splitlines()[1:] if ln.strip() and ln.strip() != PROMPT.strip()]
        what = f"{len(shown)} lines, the last {shown[-2:]}" if failed else "ran"
        RECLAIM[key] = f"{seen}; MPY with {len(holders)} MPYs holding the blocks: {what}"
    finally:
        if holders:
            command(linux, "kill " + " ".join(str(p) for p in holders))
        background(linux, linux_checks.SYSMON)


@pytest.mark.parametrize("line", list(linux_checks.SCRIPT_COMMANDS))
def test_script_command(linux, line):
    """The card's sample scripts (linux_checks.SCRIPT_COMMANDS): #!/bin/sh files on vfat,
    executable by the mode its mount gives every file, run by their names from the PATH."""
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} before {line!r}"
    out = command(linux, line)
    assert not any(e in out for e in EXEC_FAILED), out[:600]
    assert not linux_checks.missing(out, linux_checks.SCRIPT_COMMANDS[line]), out


def test_ledctl_modes(linux):
    """ledctl's sysmon modes: the running sysmon stopped (its line) before one starts in the
    mode, which then has the button (no `no button`; the button steps it on); `off` leaves
    none, its LEDs dark; `cpu` one again, for the tests after this one."""
    soc = linux.soc
    old = sysmon_pid(linux)
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for ledctl"
    mark = len(soc.uart_received())
    command(linux, "ledctl mem")
    console = wait_console(linux, "sysmon: mode mem", mark)
    assert "sysmon: stopped" in console and "sysmon: no button" not in console, console[-600:]
    assert sysmon_pid(linux) != old
    mark = len(soc.uart_received())
    press_button(soc, hold_ms=500, after_ms=500)
    wait_console(linux, "sysmon: mode load", mark)
    # Off: no sysmon to give back a large block, but each ledctl frees the one it took
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for ledctl"
    mark = len(soc.uart_received())
    command(linux, "ledctl off")
    wait_console(linux, "sysmon: stopped", mark)
    assert soc.leds & BAR == 0, hex(soc.leds)
    assert "sysmon: none" in command(linux, "ledctl")
    soc.run(ms(2000))
    assert soc.leds & BAR == 0, hex(soc.leds)
    mark = len(soc.uart_received())
    command(linux, "ledctl cpu")
    wait_console(linux, "sysmon: mode cpu", mark)
    sysmon_pid(linux)


def quiet(soc, seconds: float) -> None:
    """Runs until the console has printed nothing for `seconds` (vi idle, waiting for a key)."""
    need = int(seconds * CLK_HZ)
    end = soc.cycle + COMMAND_CYCLES
    n, since = len(soc.uart_received()), soc.cycle
    while soc.cycle - since < need:
        assert soc.cycle < end, "the console never went quiet"
        soc.run(ms(10))
        m = len(soc.uart_received())
        if m != n:
            n, since = m, soc.cycle


def vi_step(soc, step: str | tuple[str, ...]) -> None:
    """One of a ViSession's steps: its bytes back to back, a tuple's parts VI_SPLIT_S apart."""
    parts = (step,) if isinstance(step, str) else step
    t0 = soc.cycle
    for k, part in enumerate(parts):
        assert len(part) < 32, part  # the RX FIFO, which vi has emptied
        soc.uart_send(part.encode(), at=t0 + k * int(linux_checks.VI_SPLIT_S * CLK_HZ))
    soc.run(max(0, soc.uart_send_done - soc.cycle))


@pytest.mark.parametrize("session", linux_checks.VI_SESSIONS, ids=lambda s: s.name)
def test_vi(linux, session):
    """vi (the card's bbedit) on linux_checks.VI_FILE driven by a ViSession's keys (VI_CLEAR
    before the first): a new file typed and written, the file edited (search, dd, u), then
    the arrow key back to back and split VI_SPLIT_S apart against ESC alone before `[A`; cat
    shows the text each one leaves. test_card_after_linux reads the file from the card."""
    soc = linux.soc
    vi_file = linux_checks.VI_FILE
    if session is linux_checks.VI_SESSIONS[0]:
        assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for rm"
        command(linux, linux_checks.VI_CLEAR)
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for vi"
    dropped = soc.uart_rx_dropped
    start = len(soc.uart_received())
    meter = Meter(soc, MEASURED)
    send_paced(soc, f"vi {vi_file}\n".encode())
    wait_for(soc, session.ready.encode(), COMMAND_CYCLES, start)
    # The first one's exec reads bbedit from the card, the others' from the page cache
    meter.done(f"{linux.how}: vi {session.name}: to its screen")
    for step in session.steps:
        quiet(soc, linux_checks.VI_QUIET_S)
        mark = len(soc.uart_received())
        vi_step(soc, step)
    screen = wait_for(soc, PROMPT, COMMAND_CYCLES, mark)
    assert soc.uart_rx_dropped == dropped
    assert not any(e in screen for e in EXEC_FAILED), screen[:600]
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for cat"
    # command() starts past the echo, so the file's lines come first, then the prompt
    out = command(linux, f"cat {vi_file}")
    got = [ln.rstrip("\r") for ln in out.split("\n")]
    while got and not got[0]:
        got.pop(0)
    want = session.text.splitlines()
    assert got[: len(want)] == want and len(got) > len(want) and PROMPT.decode() in got[len(want)], out


def test_card_after_linux(linux, tmp_path):
    """The card as Linux leaves it after the commands' write (linux_checks.SD_TEST_FILE), vi's
    (linux_checks.VI_FILE, its LF lines, read with mtools when the host has them) and an
    umount: fsck.fat finds nothing wrong (dosfstools, which CI's job has), and the first stage
    boots the second stage from it, which reads the file Linux wrote: the stages' FAT32 and
    Linux's vfat share the card. umount is an exec of busybox: it waits for its block (sysmon's
    reclaim) first."""
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for umount's exec"
    out = command(linux, "umount /mnt/sd")
    assert "umount:" not in out and not any(e in out for e in EXEC_FAILED), out
    # Unmounted indeed (linux_checks.MOUNTS_PROBE)
    mounts = command(linux, linux_checks.MOUNTS_PROBE)
    assert "/proc proc" in mounts and linux_checks.CARD_MOUNTED not in mounts, mounts
    image = linux.soc.sd_image()
    path = tmp_path / "card.img"
    path.write_bytes(image)
    if shutil.which("fsck.fat"):
        test_fat_images.fsck(str(path), str(tmp_path))
    # vi's file (test_vi) as its last session left it: LF lines, no CR
    vi_text = linux_checks.VI_SESSIONS[-1].text.encode()
    assert vi_text in image and vi_text.replace(b"\n", b"\r\n") not in image
    # mtools before FIXTURE_MTOOLS reads a FAT32 volume this small as FAT16
    if test_fat_images.MTOOLS_SMALL_FAT32:
        assert test_fat_images.mtype(str(path), linux_checks.VI_FILE.removeprefix("/mnt/sd/")) == vi_text
    soc, _ = s1_env.boot_s2(image=image)
    name = os.path.basename(linux_checks.SD_TEST_FILE)
    assert name in s1_env.s2_cmd(soc, "ls VUX9K")
    assert s1_env.s2_cmd(soc, f"cat VUX9K/{name}").strip() == "sd-write-ok"


def paste(linux: Booted, pacer: vux_tool.Pacer | None) -> tuple[int, int]:
    """linux_checks.PASTE_LINES at the shell, back to back at 115200 baud (pacer None) or as
    the monitor sends them (tools/vux_tool.py's Pacer, on the emulator's clock), then
    PASTE_DONE once they have reached the UART; the bytes the RX FIFO dropped meanwhile, and
    how many lines' output came through whole."""
    soc = linux.soc
    data = "".join(f"{line}\n" for line in linux_checks.PASTE_LINES).encode()
    dropped = soc.uart_rx_dropped
    start = len(soc.uart_received())
    if pacer is None:
        soc.uart_send(data)
    else:
        pacer.push(data)
        for _ in range(4 * len(data)):  # a step cap: two turns a chunk
            wait = pacer.wait(soc.cycle / CLK_HZ)
            if wait is None:
                break
            if wait > 0:
                soc.run(max(1, math.ceil(wait * CLK_HZ)))
            else:
                soc.uart_send(pacer.pop(soc.cycle / CLK_HZ))
        assert pacer.wait(soc.cycle / CLK_HZ) is None, "the pacer kept bytes"
    soc.run(max(0, soc.uart_send_done - soc.cycle))
    soc.run(ms(20))  # the driver's next poll
    soc.uart_send(f"{linux_checks.PASTE_DONE}\n".encode())
    out = wait_for(soc, linux_checks.PASTE_DONE_OUT.encode(), COMMAND_CYCLES, start)
    wait_for(soc, PROMPT, COMMAND_CYCLES, start + len(out))
    return soc.uart_rx_dropped - dropped, sum(w in out for w in linux_checks.PASTE_WORDS)


def test_paste(linux):
    """A paste of 208 bytes at the shell (linux_checks.PASTE_LINES): back to back, the UART's
    32-byte RX FIFO, which the polled liteuart driver empties every 10 ms (HZ 100), drops
    bytes. That is how the SoC is, not a bug: the test wants some dropped there, the control
    that shows the paste can lose bytes at all, and records how many (test_record_measurements
    prints it). As the monitor sends them (16 bytes, 20 ms apart), none, and every line's
    output. The last test on a boot: send_paced refuses a SoC that has dropped a byte."""
    n = len(linux_checks.PASTE_LINES)
    size = sum(len(line) + 1 for line in linux_checks.PASTE_LINES)
    dropped, whole = paste(linux, None)
    PASTE[f"{linux.how}: back to back"] = f"{dropped} of {size} B dropped, {whole} of {n} lines whole"
    # The control: the emulator is deterministic, and drops 171-176 of the 208 B
    assert dropped > 0, PASTE
    dropped, whole = paste(linux, vux_tool.Pacer())
    PASTE[f"{linux.how}: monitor's pacing"] = f"{dropped} of {size} B dropped, {whole} of {n} lines whole"
    assert dropped == 0 and whole == n, PASTE


def test_card_with_read_waits():
    """A boot (load_linux) with a card that waits READ_WAITS before its blocks' tokens, and
    on it the first `ls` of a directory and exec of a program from the card (memprobe, then
    the card's bbawk), BASE_COMMANDS' 256 KB dd (their times are printed with the others), a
    write and sync, and a dd from REF_SECTORS of random data past the partition to the sectors
    after them, which then hold the same bytes: mmc_spi's token search finds every block's
    start, whatever the wait (its data CRC would refuse a block read from the wrong byte)."""
    image, dtb = linux_files()
    sd = card(image, dtb)
    src = len(sd) // 512
    assert src % 8 == 0, src  # dd's blocks of 4096 bytes
    ref = random.Random(0x5D).randbytes(REF_SECTORS * 512)
    # The destination's sectors too: the card's size (its CSD) holds them
    sd += ref + bytes(len(ref))
    linux = boot_direct(image, dtb, sd=sd, how="read waits", read_waits=list(READ_WAITS))
    assert not linux_checks.missing(linux.boot, linux_checks.BOOT_LINES), linux.boot[-2000:]
    for line in (
        "ls /mnt/sd/VUX9K",
        "/mnt/sd/VUX9K/LINUX/BIN/memprobe",
        "dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=64",
    ):
        out = command(linux, line)
        assert not linux_checks.missing(out, linux_checks.BASE_COMMANDS[line]), out

    def run(line: str) -> str:
        # An exec of busybox after the reads: it waits for its block first (sysmon's reclaim)
        assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} before {line!r}"
        out = command(linux, line)
        assert not any(e in out for e in EXEC_FAILED), out[:600]
        return out

    awk = "awk 'BEGIN{print 6*7, sqrt(2)}'"
    out = run(awk)
    assert not linux_checks.missing(out, linux_checks.TOOL_COMMANDS[awk]), out
    assert b"sd-write-ok\n" not in sd
    out = run(f"echo sd-write-ok > {linux_checks.SD_TEST_FILE}; sync")
    assert b"sd-write-ok\n" in linux.soc.sd_image(), out
    # A line shorter than the console's 80 columns with the prompt (linux_checks, TOOL_COMMANDS)
    blocks = REF_SECTORS // 8
    copy = f"dd if=/dev/mmcblk0 of=/dev/mmcblk0 bs=4096 count={blocks} skip={src // 8} seek={src // 8 + blocks}"
    assert len(copy) + len(linux_checks.PROMPT) < 80, copy
    out = run(copy)
    assert f"{blocks}+0 records out" in out, out
    run("sync")
    dst = (src + REF_SECTORS) * 512
    assert linux.soc.sd_image()[dst : dst + len(ref)] == ref, "the copy differs"


def pylib_version() -> str:
    """The version make linux-dist gave PYLIB.TAR (its MANIFEST.json)."""
    with open(os.path.join(LINUX_DIST, "MANIFEST.json")) as f:
        return json.load(f)["pylib_version"]


def test_pylib_extraction():
    """The login shell's profile and the card's pylib (docs/agents/linux.md, "MicroPython"),
    three boots (load_linux) to the prompt, each one's cycles recorded: a card without
    PYLIB.TAR and PYLIB.VER (pylib says nothing, the reference), a card with them (the first
    boot: extracted, the modules there with their long names) and that card again after an
    umount (the second: PYLIB.VER read and found the same as PYLIB/VERSION, nothing extracted,
    no exec). Then, at the second's shell, pylib sourced as the profile does: with PYLIB.TAR no
    archive and another version in PYLIB.VER it says so and leaves no PYLIB/VERSION; without
    PYLIB.VER it does nothing; the shell goes on."""
    image, dtb = linux_files()
    if linux_checks.PYLIB_TAR not in card_programs():
        pytest.skip(f"{linux_checks.PYLIB_TAR} not in make linux-dist's tree")
    version = pylib_version()
    modules = (*linux_checks.PYLIB_MODULES, "VERSION")
    labels = ("pylib: without PYLIB.TAR", "pylib: first boot, extracted", "pylib: second boot, left")

    def booted(sd: bytes, label: str) -> Booted:
        linux = boot_direct(image, dtb, sd=sd, how=label)
        PYLIB_BOOTS[label] = MEASURED[f"{label}: start to the first prompt"][0]
        assert not linux_checks.missing(linux.boot, linux_checks.BOOT_LINES), linux.boot[-2000:]
        return linux

    linux = booted(card(image, dtb, without=(linux_checks.PYLIB_TAR, linux_checks.PYLIB_VER)), labels[0])
    assert "pylib:" not in linux.boot, linux.boot[-1500:]
    assert "no-ver" in command(linux, "[ -e /run/pylib.ver ] || echo no-ver")

    linux = booted(card(image, dtb), labels[1])
    assert linux_checks.PYLIB_EXTRACTING in linux.boot, linux.boot[-1500:]
    assert linux_checks.PYLIB_EXTRACTED + version in linux.boot, linux.boot[-1500:]
    out = command(linux, f"ls {linux_checks.MPY_LIB}")
    assert not linux_checks.missing(out, modules), out
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for umount's exec"
    out = command(linux, "umount /mnt/sd")
    assert "umount:" not in out and not any(e in out for e in EXEC_FAILED), out

    linux = booted(linux.soc.sd_image(), labels[2])
    assert "pylib:" not in linux.boot, linux.boot[-1500:]
    # The profile's pylib ran: it read PYLIB.VER's version, the extracted one's
    out = command(linux, "read v </run/pylib.ver; echo v=$v")
    assert f"v={version}" in out, out
    out = command(linux, f"read v <{linux_checks.MPY_LIB}/VERSION; echo v=$v")
    assert f"v={version}" in out, out
    out = command(linux, f"ls {linux_checks.MPY_LIB}")
    assert not linux_checks.missing(out, modules), out

    pylib = ". /mnt/sd/VUX9K/LINUX/BIN/pylib; echo pylib-done"
    command(linux, f"echo junk > /mnt/sd/{linux_checks.PYLIB_TAR}")
    command(linux, f"echo 0123456789abcdef > /mnt/sd/{linux_checks.PYLIB_VER}")
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for tar's exec"
    out = command(linux, pylib)
    assert linux_checks.PYLIB_EXTRACTING in out and "pylib: PYLIB.TAR not extracted" in out, out
    assert "pylib-done" in out, out
    out = command(linux, f"[ -e {linux_checks.MPY_LIB}/VERSION ] || echo no-version")
    assert "no-version" in out, out
    # Without PYLIB.VER: nothing, not even an exec
    assert wait_large(linux), f"no free block of order {BUSYBOX_ORDER} for rm's exec"
    command(linux, f"rm /mnt/sd/{linux_checks.PYLIB_VER}")
    out = command(linux, pylib)
    assert "pylib:" not in out and "pylib-done" in out, out


def run_board_script(tmp_path, stall: bool = False) -> None:
    """scripts/hw_linux.py, the board's test, against the emulator's CLI at full speed
    (--speed 0; its times mean nothing then): a card with only the second stage, the
    kernel's files put on it in \\VUX9K\\LINUX\\, `reset` back to the first stage booting the
    second, `linux`, the boot lines and the commands. With stall, hw_linux.py talks to the CLI
    through stall_proxy, which stops the emulator while it types the commands."""
    if not os.environ.get("VUX9K_LINUX_TEST"):
        pytest.skip("make test-linux-emu runs it (VUX9K_LINUX_TEST=1)")
    if not os.path.exists(os.path.join(LINUX_DIST, "MANIFEST.json")):
        pytest.skip(f"{LINUX_DIST} missing (make linux-dist)")
    img = tmp_path / "sd.img"
    img.write_bytes(vux_tool.build_fat32_image({"VUX9K/STAGE2.BIN": s1_env.stage2()}, part_sectors=CARD_SECTORS))
    port = free_port()
    emu = subprocess.Popen(
        [EMU, "--tcp", str(port), "--sd", str(img), "--speed", "0"], cwd=REPO_ROOT, stderr=subprocess.PIPE, text=True
    )
    # The emulator's stderr read to its end in a thread (a full pipe would stop it)
    listening = threading.Event()
    log: list[str] = []

    def drain():
        assert emu.stderr
        for line in emu.stderr:
            log.append(line)
            if "waiting for a UART client" in line:
                listening.set()

    threading.Thread(target=drain, daemon=True).start()
    stalling = threading.Event()
    stalls = [0]
    timeout = BOARD_SCRIPT_STALLED_TIMEOUT_S if stall else BOARD_SCRIPT_TIMEOUT_S
    try:
        assert listening.wait(30), f"the emulator did not listen: {''.join(log)[-2000:]}"
        if stall:
            port = stall_proxy(port, emu.pid, stalling, stalls)
        script = subprocess.Popen(
            [
                sys.executable,
                "-u",  # its lines as it prints them: the stall's window follows them
                os.path.join(REPO_ROOT, "scripts", "hw_linux.py"),
                "--linux",
                LINUX_DIST,
                "--port",
                f"socket://127.0.0.1:{port}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        out: list[str] = []
        err: list[str] = []

        def read_out():
            assert script.stdout
            for line in script.stdout:
                out.append(line)
                # From the boot's [LINUX] line to vi: the boot's commands and wait_large's probes
                if line.startswith("[LINUX] "):
                    stalling.set()
                elif line.startswith(STALL_UNTIL):
                    stalling.clear()

        def read_err():
            assert script.stderr
            err.extend(script.stderr)

        readers = [threading.Thread(target=f, daemon=True) for f in (read_out, read_err)]
        for t in readers:
            t.start()
        try:
            # The Image's put and boot, then the card's busyboxes (~0.7 MB more to put, and
            # each one's first exec from the card)
            script.wait(timeout=timeout)
        finally:
            script.kill()
            script.wait()
        for t in readers:
            t.join(10)
    finally:
        stalling.clear()
        try:
            os.kill(emu.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        emu.kill()
        emu.wait()
    stdout = "".join(out)
    print(stdout[-3000:])
    if stall:
        print(f"[INFO] the emulator stopped {stalls[0]} times")
        assert stalls[0], f"the emulator was never stopped: {stdout[-3000:]}"
    assert script.returncode == 0, stdout[-3000:] + "".join(err)[-2000:]
    assert "[PUT] OK" in stdout and "Linux on the board: PASSED" in stdout


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stall_proxy(emu_port: int, pid: int, stalling: threading.Event, stalls: list[int]) -> int:
    """A TCP port between hw_linux.py and the CLI on emu_port, whose pid it stops: while
    stalling is set, the first write the host sends stops the emulator (SIGSTOP) until a line's
    end (LF) has passed on or STALL_HOLD_S have gone by (SIGCONT), as a busy host that
    deschedules the emulator would. What the host sent meanwhile waits in the socket, and the
    CLI then hands it to the UART back to back. stalls[0] counts the stops (at most
    STALL_MAX). Returns the port to connect to."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        host, _ = listener.accept()
        listener.close()
        emu = socket.create_connection(("127.0.0.1", emu_port))

        def to_host():
            try:
                while data := emu.recv(4096):
                    host.sendall(data)
                host.shutdown(socket.SHUT_WR)
            except OSError:  # either side gone: the test's end
                pass

        threading.Thread(target=to_host, daemon=True).start()
        stopped = None  # when the emulator was stopped
        try:
            while True:
                wait = None if stopped is None else max(0.0, stopped + STALL_HOLD_S - time.monotonic())
                if not select.select([host], [], [], wait)[0]:
                    os.kill(pid, signal.SIGCONT)
                    stopped = None
                    continue
                data = host.recv(4096)
                if not data:
                    break
                if stopped is None and stalling.is_set() and stalls[0] < STALL_MAX:
                    os.kill(pid, signal.SIGSTOP)
                    stopped = time.monotonic()
                    stalls[0] += 1
                emu.sendall(data)
                if stopped is not None and b"\n" in data:
                    os.kill(pid, signal.SIGCONT)
                    stopped = None
            emu.shutdown(socket.SHUT_WR)
        except OSError:  # either side gone: the test's end, which sends SIGCONT itself
            pass
        finally:
            if stopped is not None:
                try:
                    os.kill(pid, signal.SIGCONT)
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1]


def test_board_script_on_the_emulator(tmp_path):
    run_board_script(tmp_path)


def test_board_script_with_emulator_stalls(tmp_path):
    """The board's script as above, the emulator stopped while each command line is typed (up
    to its LF or STALL_HOLD_S), from the boot's commands to vi: what the host typed meanwhile
    then reaches the UART back to back, as on a busy CI host (a lost echo of a probe, CI run
    43701558484). The script must not depend on the emulator keeping up with its host's clock."""
    run_board_script(tmp_path, stall=True)


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED and not MEMORY and not SYSMON_CPU and not RECLAIM and not PASTE and not PYLIB_BOOTS:
        pytest.skip("nothing measured")
    with capsys.disabled():
        print(f"\nnoMMU Linux on the emulator (18 MHz: {CLK_HZ:,} cycles a second)")
        for label, (cycles, instret, stats) in MEASURED.items():
            cpi = cycles / max(instret, 1)
            secs = cycles / CLK_HZ
            print(
                f"  {label:<52} {cycles:>15,} cycles ({secs:6.1f} s) {instret:>13,} instret"
                f"  CPI {cpi:.1f}  {hit_rates(stats)}"
            )
            record_property(label, f"{cycles} cycles, {instret} instret, {hit_rates(stats)}")
        for how, pct in SYSMON_CPU.items():
            print(f"  {how}: sysmon's CPU over {IDLE_S} s of an idle shell: {pct:.1f}%")
            record_property(f"{how}: sysmon CPU", f"{pct:.1f}%")
        for how, text in RECLAIM.items():
            print(f"  {how}: sysmon's reclaim: {text}")
            record_property(f"{how}: reclaim", text)
        for how, text in PASTE.items():
            print(f"  {how} paste: {text}")
            record_property(f"{how} paste", text)
        # What pylib adds to the boot: its extraction at a card's first boot, its look at
        # PYLIB.VER at the next ones, each against the boot without the archive
        ref = PYLIB_BOOTS.get("pylib: without PYLIB.TAR")
        for label, cycles in PYLIB_BOOTS.items():
            if ref is not None and cycles != ref:
                added = f"{(cycles - ref) / CLK_HZ:+.1f} s"
                print(f"  {label}: {added} to the prompt against the boot without PYLIB.TAR")
                record_property(f"{label}: added", added)
        for how, rows in MEMORY.items():
            print(f"\nnoMMU Linux memory after the boot ({how})")
            for name, value in rows.items():
                print(f"  {name:<28} {value}")
                record_property(f"{how}: memory: {name}", value)
