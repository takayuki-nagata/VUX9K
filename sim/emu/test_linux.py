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
    new_soc,
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


@pytest.mark.parametrize("line", list(linux_checks.COMMANDS))
def test_command(linux, line):
    out = command(linux, line)
    assert not linux_checks.missing(out, linux_checks.COMMANDS[line]), out


def test_card_after_linux(linux, tmp_path):
    """The card as Linux leaves it after the commands' write (linux_checks.SD_TEST_FILE) and an
    umount: fsck.fat finds nothing wrong (dosfstools, which CI's job has), and the first stage
    boots the second stage from it, which reads the file Linux wrote: the stages' FAT32 and
    Linux's vfat share the card."""
    out = command(linux, "umount /mnt/sd")
    assert "umount:" not in out, out
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
    if not MEASURED and not MEMORY:
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
        for how, rows in MEMORY.items():
            print(f"\nnoMMU Linux memory after the boot ({how})")
            for name, value in rows.items():
                print(f"  {name:<28} {value}")
                record_property(f"{how}: memory: {name}", value)
