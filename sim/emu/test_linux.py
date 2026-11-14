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

The cycles, retired instructions and the caches' hit rates to the prompt (for the card: the
second stage's load, `linux` typed to its [LINUX] line, and the kernel's boot from there) and
for each command are printed by test_record_measurements. The board's test is scripts/hw_linux.py
(make test-hw-linux), which test_board_script_on_the_emulator runs here against the CLI.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
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
# The card: 8 MB, room for the Image, the device tree and the second stage
CARD_SECTORS = 16384

MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}
# The memory table of each boot path (linux_checks.memory), read right after the boot
MEMORY: dict[str, dict[str, str]] = {}
# The CPU init's sysmon took over IDLE_S seconds of an idle shell, per boot path (%)
SYSMON_CPU: dict[str, float] = {}
IDLE_S = 10
# The LEDs sysmon's bar has (led2-led5)
BAR = 0b111100


@dataclass
class Booted:
    soc: Soc
    boot: str  # the console's output to the first prompt
    how: str  # the boot path, for the measurements' labels


def card_programs() -> dict[str, bytes]:
    """make linux-dist's programs for the card (VUX9K/LINUX/BIN/), as its MANIFEST.json lists
    them."""
    try:
        with open(os.path.join(LINUX_DIST, "MANIFEST.json")) as f:
            listed = json.load(f)["card_files"]
    except FileNotFoundError:
        return {}
    progs = {}
    for rel in listed:
        if rel.startswith("VUX9K/LINUX/BIN/"):
            with open(os.path.join(LINUX_DIST, rel), "rb") as f:
                progs[rel] = f.read()
    return progs


def card(image: bytes, dtb: bytes) -> bytes:
    """The card both boot paths have in the socket (the kernel mounts it): the second stage,
    the Image and device tree where its `linux` looks for them, and the programs for the card."""
    files = {"VUX9K/STAGE2.BIN": s1_env.stage2(), "VUX9K/LINUX/Image": image, "VUX9K/LINUX/vux9k.dtb": dtb}
    return vux_tool.build_fat32_image(files | card_programs(), part_sectors=CARD_SECTORS)


def boot_direct(image: bytes, dtb: bytes) -> Booted:
    soc = new_soc()
    soc.sd_insert(card(image, dtb))
    soc.load_linux(image, dtb)
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)
    meter.done("load_linux: start to the first prompt")
    return Booted(soc, boot, "load_linux")


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


@pytest.fixture(scope="module", params=[boot_direct, boot_from_card], ids=["load_linux", "stage2"])
def linux(request) -> Booted:
    # Minutes of emulation: only make test-linux-emu runs it, not test-emu (pytest sim/emu)
    if not os.environ.get("VUX9K_LINUX_TEST"):
        pytest.skip("make test-linux-emu runs it (VUX9K_LINUX_TEST=1)")
    for path in (IMAGE, DTB):
        if not os.path.exists(path):
            pytest.skip(f"{path} missing (make linux)")
    with open(IMAGE, "rb") as image, open(DTB, "rb") as dtb:
        return request.param(image.read(), dtb.read())


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
    command(linux, "echo none > /sys/class/leds/led1/trigger")
    command(linux, "echo 1 > /sys/class/leds/led1/brightness")
    assert soc.leds & 0b10, hex(soc.leds)
    command(linux, "echo 0 > /sys/class/leds/led1/brightness")
    assert soc.leds & 0b10 == 0, hex(soc.leds)
    command(linux, "echo none > /sys/class/leds/led0/trigger")
    command(linux, "echo 0 > /sys/class/leds/led0/brightness")
    soc.run(ms(2000))
    assert soc.leds & 0b11 == 0, "led0 or led1 changed without a trigger"
    # The timer trigger: 500 ms on, 500 ms off
    command(linux, "echo timer > /sys/class/leds/led0/trigger")
    seen = set()
    for _ in range(12):
        soc.run(ms(200))
        seen.add(soc.leds & 1)
    assert seen == {0, 1}, seen
    command(linux, "echo heartbeat > /sys/class/leds/led0/trigger")
    command(linux, "echo mmc0 > /sys/class/leds/led1/trigger")
    assert "[mmc0]" in command(linux, "cat /sys/class/leds/led1/trigger")


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
    """init's system monitor on the GPIO register's led2-led5 (soc.leds): its CPU over an idle
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


def test_card_after_linux(linux, tmp_path):
    """The card as Linux leaves it after the commands' write (linux_checks.SD_TEST_FILE) and an
    umount: fsck.fat finds nothing wrong (dosfstools, which CI's job has), and the first stage
    boots the second stage from it, which reads the file Linux wrote: the stages' FAT32 and
    Linux's vfat share the card."""
    out = command(linux, "umount /mnt/sd")
    assert "umount:" not in out, out
    # Unmounted indeed (linux_checks.MOUNTS_PROBE)
    mounts = command(linux, linux_checks.MOUNTS_PROBE)
    assert "/proc proc" in mounts and linux_checks.CARD_MOUNTED not in mounts, mounts
    image = linux.soc.sd_image()
    if shutil.which("fsck.fat"):
        path = tmp_path / "card.img"
        path.write_bytes(image)
        test_fat_images.fsck(str(path), str(tmp_path))
    soc, _ = s1_env.boot_s2(image=image)
    name = os.path.basename(linux_checks.SD_TEST_FILE)
    assert name in s1_env.s2_cmd(soc, "ls VUX9K")
    assert s1_env.s2_cmd(soc, f"cat VUX9K/{name}").strip() == "sd-write-ok"


def test_board_script_on_the_emulator(tmp_path):
    """scripts/hw_linux.py, the board's test, against the emulator's CLI at full speed
    (--speed 0; its times mean nothing then): a card with only the second stage, the
    kernel's files put on it in \\VUX9K\\LINUX\\, `reset` back to the first stage booting the
    second, `linux`, the boot lines and the commands."""
    if not os.environ.get("VUX9K_LINUX_TEST"):
        pytest.skip("make test-linux-emu runs it (VUX9K_LINUX_TEST=1)")
    if not os.path.exists(os.path.join(LINUX_DIST, "MANIFEST.json")):
        pytest.skip(f"{LINUX_DIST} missing (make linux-dist)")
    img = tmp_path / "sd.img"
    img.write_bytes(vux_tool.build_fat32_image({"VUX9K/STAGE2.BIN": s1_env.stage2()}, part_sectors=CARD_SECTORS))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
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
    try:
        assert listening.wait(30), f"the emulator did not listen: {''.join(log)[-2000:]}"
        r = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "scripts", "hw_linux.py"),
                "--linux",
                LINUX_DIST,
                "--port",
                f"socket://127.0.0.1:{port}",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        emu.kill()
        emu.wait()
    print(r.stdout[-3000:])
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "[PUT] OK" in r.stdout and "Linux on the board: PASSED" in r.stdout


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED and not MEMORY and not SYSMON_CPU:
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
        for how, rows in MEMORY.items():
            print(f"\nnoMMU Linux memory after the boot ({how})")
            for name, value in rows.items():
                print(f"  {name:<28} {value}")
                record_property(f"{how}: memory: {name}", value)
