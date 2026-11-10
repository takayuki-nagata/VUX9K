# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""What make linux's kernel must print on the console, shared by its emulator test
(sim/emu/test_linux.py) and its board test (scripts/hw_linux.py): the lines of its boot to
busybox's shell, and commands typed at the shell with what each must answer (one of them a
builtin, the others busybox executed again: FLAT, copied into RAM at each exec)."""

PROMPT = "/ # "
BOOT_LINES = (
    "Linux version 7.3",
    "clint: clint@20000000: timer running at 18000000 Hz",
    "printk: legacy console [liteuart0] enabled",
    "Run /init as init process",
    "hush - the humble shell",
)
# command -> what its output must contain
COMMANDS = {
    "echo hello-vux9k": ("hello-vux9k",),
    "uname -a": ("Linux (none) 7.3.0",),
    "ls /": ("bin", "dev", "proc", "sbin"),
    "cat /proc/cpuinfo": ("rv32ima",),
}


def missing(out: str, wanted) -> list[str]:
    """What of wanted is not in out."""
    return [w for w in wanted if w not in out]
