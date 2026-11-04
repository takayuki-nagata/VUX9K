# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
noMMU Linux (make linux: linux_workspace/, the kernel with buildroot's busybox initramfs built
in) on the emulator's Linux profile, which is ahead of the RTL (the liteuart view;
docs/agents/linux.md): the kernel Image and the device tree loaded into the PSRAM
(Soc.load_linux), the boot to busybox's shell on the console, then commands typed at it, one of
them a builtin and the others busybox executed again (FLAT, copied into RAM at each exec).

The cycles, retired instructions and the caches' hit rates to the prompt and for each command
are printed by test_record_measurements. Nothing here runs on the board: the RTL has none of the
profile's additions yet.
"""

import os

import pytest
from vux9k import CLK_HZ, REPO_ROOT, Meter, hit_rates, new_soc, send_paced, wait_for

LINUX_DIR = os.path.join(REPO_ROOT, "build", "linux")
IMAGE = os.path.join(LINUX_DIR, "arch", "riscv", "boot", "Image")
DTB = os.path.join(LINUX_DIR, "vux9k-linux-emu.dtb")
PROMPT = b"/ # "
# Upper bounds, not measurements (test_record_measurements prints those): the boot to the
# prompt took ~224M cycles and a command that executes busybox ~15M (2026-11-01)
BOOT_CYCLES = 10_000_000_000
COMMAND_CYCLES = 2_000_000_000

MEASURED: dict[str, tuple[int, int, dict[str, int]]] = {}


@pytest.fixture(scope="module")
def linux():
    # Minutes of emulation: only make test-linux-emu runs it, not test-emu (pytest sim/emu)
    if not os.environ.get("VUX9K_LINUX_TEST"):
        pytest.skip("make test-linux-emu runs it (VUX9K_LINUX_TEST=1)")
    for path in (IMAGE, DTB):
        if not os.path.exists(path):
            pytest.skip(f"{path} missing (make linux)")
    soc = new_soc("linux")
    with open(IMAGE, "rb") as image, open(DTB, "rb") as dtb:
        soc.load_linux(image.read(), dtb.read())
    meter = Meter(soc, MEASURED)
    boot = wait_for(soc, PROMPT, timeout_cycles=BOOT_CYCLES)
    meter.done("boot: start to the first prompt (load_linux)")
    return soc, boot


def command(soc, line: str) -> str:
    """One command typed at the shell; what it prints up to the next prompt, its echo
    included. Measured from the first byte sent."""
    start = len(soc.uart_received())
    meter = Meter(soc, MEASURED)
    send_paced(soc, line.encode() + b"\n")
    out = wait_for(soc, PROMPT, timeout_cycles=COMMAND_CYCLES, start=start + len(line))
    meter.done(f"command: {line}")
    return out


def test_boot_banner(linux):
    _, boot = linux
    assert "Linux version 7.3" in boot
    assert "clint: clint@20000000: timer running at 18000000 Hz" in boot
    assert "printk: legacy console [liteuart0] enabled" in boot
    assert "Run /init as init process" in boot
    assert "hush - the humble shell" in boot


def test_builtin_echo(linux):
    soc, _ = linux
    assert "hello-vux9k" in command(soc, "echo hello-vux9k")


def test_uname(linux):
    soc, _ = linux
    assert "Linux (none) 7.3.0" in command(soc, "uname -a")


def test_ls_root(linux):
    soc, _ = linux
    out = command(soc, "ls /")
    for name in ("bin", "dev", "proc", "sbin"):
        assert name in out


def test_proc_cpuinfo(linux):
    soc, _ = linux
    assert "rv32ima" in command(soc, "cat /proc/cpuinfo")


def test_record_measurements(record_property, capsys):
    """Asserts nothing about speed: prints what the tests above measured (run last)."""
    if not MEASURED:
        pytest.skip("nothing measured")
    with capsys.disabled():
        print(f"\nnoMMU Linux on the emulator's Linux profile (18 MHz: {CLK_HZ:,} cycles a second)")
        for label, (cycles, instret, stats) in MEASURED.items():
            cpi = cycles / max(instret, 1)
            secs = cycles / CLK_HZ
            print(
                f"  {label:<44} {cycles:>15,} cycles ({secs:6.1f} s) {instret:>13,} instret"
                f"  CPI {cpi:.1f}  {hit_rates(stats)}"
            )
            record_property(label, f"{cycles} cycles, {instret} instret, {hit_rates(stats)}")
