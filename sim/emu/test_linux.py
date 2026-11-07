# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
noMMU Linux (make linux: linux_workspace/, the kernel with buildroot's busybox initramfs built
in) on the emulator's real profile, the SoC as synthesized (docs/agents/linux.md), booted two
ways: the kernel Image and the device tree loaded into the PSRAM (Soc.load_linux), and the
same files on a FAT32 card (vux_tool's mkfat, `\\VUX9K\\LINUX\\Image` and `vux9k.dtb`) that the
second stage, booted by the first from the card, loads and starts with `linux`, as on the
board. Each boots to busybox's shell on the console, then commands are typed at it, one of
them a builtin and the others busybox executed again (FLAT, copied into RAM at each exec).

The cycles, retired instructions and the caches' hit rates to the prompt (for the card: the
second stage's load, `linux` typed to its [LINUX] line, and the kernel's boot from there) and
for each command are printed by test_record_measurements. Nothing here runs on the board.
"""

import os
from dataclasses import dataclass

import pytest
import s1_env
from vux9k import CLK_HZ, REPO_ROOT, UART_BIT, Meter, Soc, hit_rates, new_soc, send_paced, vux_tool, wait_for

LINUX_DIR = os.path.join(REPO_ROOT, "build", "linux")
IMAGE = os.path.join(LINUX_DIR, "arch", "riscv", "boot", "Image")
DTB = os.path.join(LINUX_DIR, "vux9k.dtb")
PROMPT = b"/ # "
# Upper bounds, not measurements (test_record_measurements prints those): the boot to the
# prompt took ~224M cycles and a command that executes busybox ~15M (2026-11-01); the second
# stage reads the 3.2 MB Image at ~70 cycles a byte, ~230M (2026-11-07)
BOOT_CYCLES = 10_000_000_000
COMMAND_CYCLES = 2_000_000_000
LOAD_CYCLES = 3_000_000_000
# The card: 8 MB, room for the Image, the device tree and the second stage
CARD_SECTORS = 16384

MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}


@dataclass
class Booted:
    soc: Soc
    boot: str  # the console's output to the first prompt
    how: str  # the boot path, for the measurements' labels


def boot_direct(image: bytes, dtb: bytes) -> Booted:
    soc = new_soc()
    soc.load_linux(image, dtb)
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)
    meter.done("load_linux: start to the first prompt")
    return Booted(soc, boot, "load_linux")


def boot_from_card(image: bytes, dtb: bytes) -> Booted:
    files = {"VUX9K/STAGE2.BIN": s1_env.stage2(), "VUX9K/LINUX/Image": image, "VUX9K/LINUX/vux9k.dtb": dtb}
    soc, _ = s1_env.boot_s2(image=vux_tool.build_fat32_image(files, part_sectors=CARD_SECTORS))
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
    assert "Linux version 7.3" in linux.boot
    assert "clint: clint@20000000: timer running at 18000000 Hz" in linux.boot
    assert "printk: legacy console [liteuart0] enabled" in linux.boot
    assert "Run /init as init process" in linux.boot
    assert "hush - the humble shell" in linux.boot


def test_builtin_echo(linux):
    assert "hello-vux9k" in command(linux, "echo hello-vux9k")


def test_uname(linux):
    assert "Linux (none) 7.3.0" in command(linux, "uname -a")


def test_ls_root(linux):
    out = command(linux, "ls /")
    for name in ("bin", "dev", "proc", "sbin"):
        assert name in out


def test_proc_cpuinfo(linux):
    assert "rv32ima" in command(linux, "cat /proc/cpuinfo")


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED:
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
