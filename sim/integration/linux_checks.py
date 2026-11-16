# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""What make linux's kernel must print on the console, shared by its emulator test
(sim/emu/test_linux.py) and its board test (scripts/hw_linux.py): the lines of its boot to
busybox's shell, and commands typed at the shell with what each must answer (one of them a
builtin, the others busybox or this tree's programs executed: FLAT, copied into RAM at each
exec)."""

import re
from dataclasses import dataclass

PROMPT = "/ # "
# A kernel message on the console: printk's timestamp (`[%5lu.%06lu] `) to the end of its line,
# which can start anywhere in what the shell prints, in the middle of a command line's echo too
# (random's `crng init done` at ~255 s of guest time, where the board's test on the emulator
# types its commands), so the pattern isn't anchored to a line's start. Only that form, so a
# command's own output (none of COMMANDS' prints a timestamp) is left alone
KERNEL_LINE = re.compile(r"\[ *\d+\.\d{6}\] [^\r\n]*\r?\n")
# The system monitor init starts (linux_workspace/userland/init/), its name in ps
SYSMON = "sysmon"
SYSMON_PATH = "/usr/sbin/sysmon"
# A file the commands write on the card
SD_TEST_FILE = "/mnt/sd/VUX9K/SDTEST.TXT"
BOOT_LINES = (
    "Linux version 7.3",
    "clint: clint@20000000: timer running at 18000000 Hz",
    "printk: legacy console [liteuart0] enabled",
    "Run /init as init process",
    # This tree's init (linux_workspace/userland/init/)
    "init: /dev/mmcblk0p1 mounted on /mnt/sd",
    "hush - the humble shell",
)
# command -> what its output must contain: the shell and the card (BASE_COMMANDS), then the
# LEDs and sysmon (LED_COMMANDS), which sim/emu/test_linux.py runs with its own tests between
BASE_COMMANDS = {
    "echo hello-vux9k": ("hello-vux9k",),
    "uname -a": ("Linux (none) 7.3.0",),
    "ls /": ("bin", "dev", "proc", "sbin"),
    "cat /proc/cpuinfo": ("rv32ima",),
    # The SD card (mmc_spi on spi-vux9k-sd) and its MBR's FAT32 partition, which init mounts
    # (linux_workspace/userland/init/)
    "cat /proc/partitions": ("mmcblk0", "mmcblk0p1"),
    # Without mount options, vfat's fmask is init's umask: every file executable (the card's
    # scripts, docs/agents/linux.md, "Text tools and vi")
    "cat /proc/mounts": ("/dev/mmcblk0p1 /mnt/sd vfat ", ",fmask=0022,"),
    # The card's programs (make linux-dist's VUX9K/LINUX/BIN/) are on the login shell's PATH,
    # after the initramfs': this tree's own program memprobe (linux_workspace/userland/memprobe/:
    # the largest free block allocated, with a copy of itself in RAM), on the card only, by its
    # name and its path
    "echo $PATH": ("/mnt/sd/VUX9K/LINUX/BIN",),
    # The rest of /etc/profile (linux_workspace/buildroot/board/post-build.sh); no cd, so the
    # prompt stays PROMPT
    "echo $HOME $EDITOR $TERM": ("/mnt/sd vi vt100",),
    "memprobe": ("memprobe: MemFree ", "), allocated"),
    "ls /mnt/sd/VUX9K": ("LINUX", "STAGE2.BIN"),
    "/mnt/sd/VUX9K/LINUX/BIN/memprobe": ("memprobe: MemFree ", "), allocated"),
    # A write to the card, read back by another command (the first's echo holds its text)
    f"echo sd-write-ok > {SD_TEST_FILE}; sync": (),
    f"cat {SD_TEST_FILE}": ("sd-write-ok",),
    # The card's read speed (the command's time): 256 KB from the start of the card
    "dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=64": ("64+0 records in",),
}
# MicroPython on the card (docs/agents/linux.md, "MicroPython"), run by its launcher PYTHON (the
# card's script python, which executes MPY with its arguments): its modules extracted from the
# card's PYLIB_TAR into MPY_LIB by the login shell's profile (pylib, when PYLIB_VER, the
# archive's version, is not the one there), which also sets MICROPYPATH; MPY's own path without
# it (a subshell that executes the launcher in its place); bc_clone_py's self-test from the
# card's .mpy; Python scripts on the card that the shell's echo writes (one line each: hush's
# here-documents would show their own prompt), the first with arguments (one with a space and
# quotes, through the launcher's "$@"), one writing an LED through the LED class (MPY_LED, its
# trigger given back after), one growing the heap (MPY_HEAP_BYTES of bytearrays,
# micropython.mem_info's `GC: total` every 8: heap_problems). Each exec of MPY copies it into a
# free block of order MPY_ORDER (~512 KB); the launcher drops the caches when none is free, and
# without one after that prints PYTHON_NO_BLOCK and exits with PYTHON_NO_BLOCK_STATUS, MPY not
# run. The launcher is a #!/bin/sh script, an exec of busybox (BUSYBOX_ORDER) first, which the
# tests wait for (runs_mpy). MPY's first exec reads ~0.5 MB from the card: MPY_TIMEOUT_S on the
# board
MPY_LIB = "/mnt/sd/VUX9K/LINUX/PYLIB"
PYLIB_TAR = "VUX9K/LINUX/PYLIB.TAR"
PYLIB_VER = "VUX9K/LINUX/PYLIB.VER"
PYLIB_MODULES = tuple(f"{m}.mpy" for m in ("bc_clone", "bc_eval", "bc_math", "bc_parser", "bc_selftest"))
# What the profile's pylib prints when it extracts PYLIB_TAR (nothing when it skips it)
PYLIB_EXTRACTING = "pylib: extracting PYLIB.TAR"
PYLIB_EXTRACTED = "pylib: PYLIB.TAR extracted, version "
MPY_VERSION = "MicroPython v1.29.0"
PYTHON = "python"
# The launcher's ORDER and KB, its message and exit status (sim/emu/test_linux_checks.py holds
# them to the script)
MPY_ORDER = 7
PAGE_KB = 4
PYTHON_NO_BLOCK = f"python: no free {PAGE_KB << MPY_ORDER} kB block for MicroPython; memory is fragmented, reboot"
PYTHON_NO_BLOCK_STATUS = 75
MPY_TIMEOUT_S = 180
MPY_ARGS_PY, MPY_LED_PY, MPY_HEAP_PY = (f"/mnt/sd/{n}.PY" for n in ("ARGS", "LED", "HEAP"))
MPY_LED = "led5"
MPY_HEAP_BYTES = 200 * 1024
# The most the heap may grow by at a time: an order-5 block (vux9k_heap.c's VUX9K_SPLIT_MAX is
# that less malloc's page), so that it leaves the larger blocks to the execs
HEAP_STEP_MAX = 128 * 1024
# What no MPY command may print (the launcher's refusal too)
MPY_UNWANTED = ("Traceback", "[FAIL]", "MemoryError", PYTHON_NO_BLOCK)


def echo_lines(path: str, lines: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Commands that write lines into path with the shell's echo: the first truncates."""
    return {f'echo "{line}" {">" if k == 0 else ">>"} {path}': () for k, line in enumerate(lines)}


MPY_COMMANDS = {
    'python -c "import sys; print(sys.version)"': (MPY_VERSION,),
    "echo $MICROPYPATH": (MPY_LIB,),
    # The modules' long names, which mkfat and the stages can't write
    f"ls {MPY_LIB}": (*PYLIB_MODULES, "VERSION"),
    '(unset MICROPYPATH; exec python -c "import sys; print(sys.path)")': (f"'{MPY_LIB}'",),
    'python -c "import bc_selftest; bc_selftest.run()"': ("ALL BC SELF-TESTS PASSED",),
    **echo_lines(MPY_ARGS_PY, ("import sys", "print('args', sys.argv[1:], 6 * 7)")),
    f"python {MPY_ARGS_PY} x 'y \"z\"'": ("args ['x', 'y \"z\"'] 42",),
    **echo_lines(
        MPY_LED_PY,
        (
            f"L = '/sys/class/leds/{MPY_LED}/'",
            "def w(f, v):",
            " o = open(L + f, 'w'); o.write(v); o.close()",
            "w('trigger', 'none'); w('brightness', '1')",
            "b = open(L + 'brightness').read()",
            f"print('{MPY_LED}', b.strip())",
        ),
    ),
    f"python {MPY_LED_PY}": (f"{MPY_LED} 1",),
    f"echo mmc0 > /sys/class/leds/{MPY_LED}/trigger": (),
    **echo_lines(
        MPY_HEAP_PY,
        (
            "import micropython as m",
            "l = []",
            f"for i in range({MPY_HEAP_BYTES // 1024}):",
            " l.append(bytearray(1024))",
            " if i % 8 == 0: m.mem_info()",
            "m.mem_info()",
            "print('heap', len(l))",
        ),
    ),
    f"python {MPY_HEAP_PY}": (f"heap {MPY_HEAP_BYTES // 1024}",),
}
# The REPL after MPY_COMMANDS (sim/emu/test_linux.py's test_mpy_repl, hw_linux.py's mpy_repl):
# the launcher alone, MPY's banner and prompt, a line and its value, Ctrl-D back to the shell
MPY_REPL = PYTHON
MPY_REPL_PROMPT = ">>> "
MPY_REPL_LINE = "1+1"
MPY_REPL_OUT = "2"
MPY_REPL_EXIT = "\x04"


def runs_mpy(cmd: str) -> bool:
    """Whether the command executes MPY, itself or by its launcher PYTHON."""
    return not {"MPY", PYTHON}.isdisjoint(cmd.replace("(", " ").split())


def heap_totals(out: str) -> list[int]:
    """micropython.mem_info()'s `GC: total: N` values, in order."""
    return [int(n) for n in re.findall(r"GC: total: (\d+),", out)]


def heap_problems(out: str) -> list[str]:
    """What is wrong with MPY_HEAP_PY's output: the heap must have grown to hold MPY_HEAP_BYTES,
    each time by at most HEAP_STEP_MAX."""
    totals = heap_totals(out)
    if len(totals) < 2:
        return [f"no micropython.mem_info() lines: {out[-300:]!r}"]
    steps = [b - a for a, b in zip(totals, totals[1:], strict=False) if b != a]
    problems = []
    if not steps or totals[-1] < MPY_HEAP_BYTES:
        problems.append(f"the heap did not grow to {MPY_HEAP_BYTES:,} B: {totals}")
    big = [s for s in steps if abs(s) > HEAP_STEP_MAX]
    if big:
        problems.append(f"the heap grew by more than {HEAP_STEP_MAX:,} B at a time: {steps}")
    return problems


def problems(cmd: str, out: str) -> list[str]:
    """What a command's output shows wrong beyond what it must contain (COMMANDS): MPY's
    tracebacks and failures (MPY_UNWANTED), the heap's growth (heap_problems)."""
    found = []
    if runs_mpy(cmd):
        found += [f"{cmd}: {u!r} in its output" for u in MPY_UNWANTED if u in out]
    if cmd == f"{PYTHON} {MPY_HEAP_PY}":
        found += [f"{cmd}: {p}" for p in heap_problems(out)]
    return found


LED_COMMANDS = {
    # The board's LEDs in the LED class (the device tree's gpio-leds on upstream's gpio-mmio),
    # led0 and led5 with the kernel's triggers, led1-led4 sysmon's
    "ls /sys/class/leds": tuple(f"led{n}" for n in range(6)),
    "cat /sys/class/leds/led0/trigger": ("[heartbeat]",),
    "cat /sys/class/leds/led5/trigger": ("[mmc0]",),
    # The system monitor (linux_workspace/userland/sysmon/), which init started at boot, and a
    # second one for two intervals: the first holds the button's line, so this one has none
    "ps": (SYSMON,),
    "sysmon -m mem -i 200 -n 2 -v": (
        "sysmon: no button",
        "sysmon: mode mem",
        "sysmon: mem ",
        "% bar ",
        "sysmon: stopped",
    ),
}
# The text tools (docs/agents/linux.md, "Text tools and vi"): the card's busyboxes, each run by
# the link in the initramfs' /usr/bin that names an applet of it, on two files the shell's
# builtins write into TOOLS_DIR. One command at a time, no pipe: hush re-executes busybox for a
# pipe's builtins, and a pipe's processes each need a block at once. Each line stays short of
# the console's 80 columns with the prompt: a longer one wraps in the shell's line editor,
# which then echoes more than was typed.
TOOLS_DIR = "/mnt/sd/TOOLS"
TOOLS_A, TOOLS_B, TOOLS_SORTED, TOOLS_COPY = (f"{TOOLS_DIR}/{n}" for n in "ABSC")
TOOLS_TAR = f"{TOOLS_DIR}/T.TAR"
TOOL_COMMANDS = {
    f"rm -rf {TOOLS_DIR}; mkdir {TOOLS_DIR}": (),
    f"for w in pear apple pear fig; do echo $w >>{TOOLS_A}; done": (),
    f"for w in pear apple plum fig; do echo $w >>{TOOLS_B}; done": (),
    # bbgrep
    f"grep -n apple {TOOLS_A}": ("2:apple",),
    f"egrep -v 'pear|fig' {TOOLS_B}": ("apple", "plum"),
    # bbsed
    f"sed -n 's/^p\\(.*\\)/P-\\1/p' {TOOLS_B}": ("P-ear", "P-lum"),
    # bbsort: uniq counts the two pears only once sort has put them together
    f"sort {TOOLS_A} >{TOOLS_SORTED}; uniq -c {TOOLS_SORTED}": ("2 pear",),
    f"cut -c2-3 {TOOLS_B}": ("pp", "lu"),
    f"tr a-z A-Z <{TOOLS_B}": ("APPLE", "PLUM"),
    # bbawk (its libm too), bbcalc
    "awk 'BEGIN{print 6*7, sqrt(2)}'": ("42 1.41421",),
    "expr 6 '*' 7": ("42",),
    # The small dc runs -e SCRIPT (its other arguments are files)
    "dc -e '1234 5 * p'": ("6170",),
    # bbfiles: the card's files, the two files' difference
    "find /mnt/sd/VUX9K -name '*.BIN'": ("/mnt/sd/VUX9K/STAGE2.BIN",),
    f"diff {TOOLS_A} {TOOLS_B}": ("@@ -1,4 +1,4 @@", "-pear", "+plum"),
    f"cmp {TOOLS_A} {TOOLS_B}": ("differ:", "line 3"),
    "stty -a": ("speed ", " baud;"),
    # bbtar: an archive of the two files made and one of them read back out of it
    f"tar -cf {TOOLS_TAR} -C {TOOLS_DIR} A B": (),
    f"tar -xOf {TOOLS_TAR} B": ("plum",),
    # bbedit: less is cat when its output is not a terminal (vi needs keys: not here)
    f"less {TOOLS_B} >{TOOLS_COPY}": (),
    f"cmp {TOOLS_B} {TOOLS_COPY}; echo cmp-$?": ("cmp-0",),
    # The initramfs' busybox (order 6) after all of them
    "ls /mnt/sd": ("TOOLS", "VUX9K"),
}
# Pipes of the text tools on TOOL_COMMANDS' files: one with the shell's syntax, whose { } hush
# runs in busybox executed again (order 6; docs/agents/linux.md, "Text tools and vi"), and one
# of the tools only. Each process holds its block at once, so they need the large free blocks
# that sysmon's reclaim of the page cache gives back after the card's reads (docs/agents/
# linux.md, "Memory budget"). uniq -c's counts are right-aligned: the lines end with these.
PIPE_COMMANDS = {
    "{ echo b; echo a; echo b; } | grep . | sort | uniq -c": ("1 a", "2 b"),
    f"grep . {TOOLS_A} | sort | uniq -c": ("1 apple", "1 fig", "2 pear"),
}
# The card's sample scripts (linux_workspace/userland/scripts/, package vux9k-scripts: #!/bin/sh
# in VUX9K/LINUX/BIN/, on the PATH), by their names. Each is the initramfs' busybox executed
# (order 6), meminfo then awk in its place. ledctl's modes (sysmon stopped and started again)
# print sysmon's lines after the prompt: sim/emu/test_linux.py's test_ledctl_modes.
SCRIPT_COMMANDS = {
    "ls -l /mnt/sd/VUX9K/LINUX/BIN/meminfo": ("-rwxr-xr-x ",),
    "meminfo": ("MemTotal ", "MemFree ", "free blocks (order:count) 0:", "largest free block: ", " kB (order "),
    "ledctl": ("led0: heartbeat", "led1: none", "led5: mmc0", "sysmon: "),
    "ledctl led0 timer; ledctl": ("led0: timer",),
    "ledctl 0 heartbeat; ledctl": ("led0: heartbeat",),
    "ledctl led9 timer": ("usage: ledctl ",),
    # pylib by hand (the profile sources it at the boot: MPY_COMMANDS' modules), with nothing to
    # extract again
    "pylib; echo pylib-$?": ("pylib-0",),
}
# What the board's scripts/hw_linux.py types, in this order; sim/emu/test_linux.py runs each
# part with its own tests between. MicroPython's right after the boot's, where the boot's free
# blocks of MPY_ORDER are (the launcher's drop gives one back only if the page cache is what
# split it)
COMMANDS = BASE_COMMANDS | MPY_COMMANDS | LED_COMMANDS | TOOL_COMMANDS | PIPE_COMMANDS | SCRIPT_COMMANDS
# The card's busyboxes and their applets (linux_workspace/userland/bbtools/<name>.config; the
# table in docs/agents/linux.md, "Text tools and vi"): the board's test times each one's first
# exec, the first of TOOL_COMMANDS that runs one of its applets (first_execs)
TOOL_BUSYBOXES = {
    "bbgrep": ("grep", "egrep"),
    "bbsed": ("sed",),
    "bbsort": ("sort", "uniq", "cut", "tr", "tail"),
    "bbawk": ("awk",),
    "bbcalc": ("expr", "dc"),
    "bbfiles": ("find", "xargs", "diff", "cmp", "stty"),
    "bbtar": ("tar",),
    "bbedit": ("vi", "less"),
}
# The order of the initramfs' busybox's exec (docs/agents/linux.md, "Memory budget"), the least
# sysmon's reclaim of the page cache keeps (its BUSYBOX_ORDER; by default it keeps MPY_ORDER,
# its RECLAIM_ORDER)
BUSYBOX_ORDER = 6
# sysmon's interval (its -i default, seconds) and every how many intervals its reclaim looks
# (linux_workspace/userland/sysmon/src/lib.rs: RECLAIM_EVERY; sim/emu/test_linux_checks.py
# holds these and the orders to it)
SYSMON_INTERVAL_S = 1
SYSMON_RECLAIM_EVERY = 3
# How long a test waits before an exec for sysmon's reclaim to give a free block back, in the
# guest's seconds: two of its looks (the next one, and one more for intervals a busy CPU
# stretches: sysmon sleeps its interval, then works) and RECLAIM_DROP_S for the drop (~1.1 s
# for ~600 kB of cache at 18 MHz), the freed pages reaching the free lists and the wait's 1 s
# between probes. scripts/hw_linux.py counts the guest's seconds by its /proc/uptime
# (UPTIME_PROBE), as on the emulator with its stops the host's clock runs ahead of the guest's,
# and gives up after RECLAIM_WAIT_WALL_S of the host's in any case
RECLAIM_DROP_S = 4
RECLAIM_WAIT_S = 2 * SYSMON_RECLAIM_EVERY * SYSMON_INTERVAL_S + RECLAIM_DROP_S
RECLAIM_WAIT_WALL_S = 120
UPTIME_PROBE = "read u i </proc/uptime; echo up=$u"


def parse_uptime(out: str) -> float | None:
    """UPTIME_PROBE's answer, the guest's uptime in seconds (not its echo's `$u`)."""
    m = re.search(r"up=(\d+\.\d+)", out)
    return float(m.group(1)) if m else None


def first_execs(commands) -> dict[str, str]:
    """{busybox on the card: the first of commands whose program is one of its applets}."""
    found: dict[str, str] = {}
    for cmd in commands:
        prog = cmd.split()[0]
        for bb, applets in TOOL_BUSYBOXES.items():
            if prog in applets and bb not in found:
                found[bb] = cmd
    return found


# vi (the card's bbedit: docs/agents/linux.md, "Text tools and vi") driven by its keys, for the
# emulator's test (sim/emu/test_linux.py) and the board's: VI_CLEAR first, then for each session
# `vi VI_FILE` at the shell, its status line awaited (ViSession.ready: vi has asked the
# terminal for its size, which a serial console can't give, waited VI_ASK_S for an answer and
# read the file; a key before that is lost), then its steps one at a time, then the prompt
# after its :wq, then `cat VI_FILE`, which shows the session's text. A step's bytes go back to
# back; before each step the console has been quiet for VI_QUIET_S, longer than busybox's wait
# for an escape sequence's next byte (VI_ESC_WAIT_S, libbb/read_key.c's poll of 50 ms): so an
# ESC that ends a step is the key ESC, and ESC [ A in a step the arrow up. A step that is a
# tuple sends its parts VI_SPLIT_S apart, as the monitor's pacing may split a key's sequence
# (tools/vux_tool.py, PASTE_GAP_MS): still one key. Each step is shorter than the UART's RX
# FIFO (32 bytes), which vi, idle, has emptied. vi writes LF only: Enter is CR in its raw
# mode, and it stores CR as LF.
VI_FILE = "/mnt/sd/VUX9K/VITEST.TXT"
VI_CLEAR = f"rm -f {VI_FILE}"
VI_ESC_WAIT_S = 0.05
VI_ASK_S = 0.1
VI_QUIET_S = 0.3
VI_SPLIT_S = 0.02
ESC = "\x1b"
UP = ESC + "[A"


@dataclass(frozen=True)
class ViSession:
    name: str
    steps: tuple[str | tuple[str, ...], ...]
    text: str  # VI_FILE afterwards

    # vi's status line in command mode: "- FILE LINE/LINES PERCENT%"
    ready = f"- {VI_FILE} "


VI_SESSIONS = (
    # A new file: insert, ESC, write and quit. No CR after the last line: vi's buffer of a new
    # file is one empty line already
    ViSession("new", ("ione\rtwo\rthree\rfour" + ESC, ":wq\r"), "one\ntwo\nthree\nfour\n"),
    # The file again: two lines deleted, each found by a search, the second deletion undone
    ViSession("edit", ("/two\r", "dd", "/four\r", "dd", "u", ":wq\r"), "one\nthree\nfour\n"),
    # The keys: G to the last line, the arrow up back to back and split VI_SPLIT_S apart (two
    # lines up), dd; then ESC alone and `[AX` ESC: `[` is no command, A appends X to the line.
    # Had the ESC and `[A` been one key (the arrow up, at the top already), X would be the
    # command X (delete backwards) instead; had the split arrow been ESC alone, `[A` would have
    # appended the keys after it to "one"
    ViSession("keys", ("G", UP, (ESC, "[", "A"), "dd", ESC, "[AX" + ESC, ":wq\r"), "threeX\nfour\n"),
)

# A paste at the shell (sim/emu/test_linux.py's test_paste): PASTE_LINES each with its LF, 208
# bytes, then PASTE_DONE, whose output PASTE_DONE_OUT ends it. A line's output is PASTE_WORDS'
# word, which its echo doesn't hold (the backslash): the words in the output are the lines that
# came through whole. A byte lost leaves no quote open, at most a line joined to the next.
PASTE_WORDS = tuple(f"paste-{n:02d}-abcdefghij" for n in range(8))
PASTE_LINES = tuple("echo " + w.replace("-", "\\-", 1) for w in PASTE_WORDS)
PASTE_DONE = "echo paste\\done"
PASTE_DONE_OUT = "pastedone"
# What the board's test does to its card after COMMANDS and VI_SESSIONS: the files removed and
# the card unmounted, so the FAT's dirty bit is clear when the bitstream is loaded again (the
# emulator's card is thrown away: sim/emu/test_linux.py checks it with the first and second
# stage after an umount; the extracted PYLIB/ stays, as PYLIB.TAR does). Three rm lines: one
# would reach the console's 80 columns with the prompt
CARD_CLEANUP = (
    f"rm -r {SD_TEST_FILE} {TOOLS_DIR}",
    f"rm {MPY_ARGS_PY} {MPY_LED_PY} {MPY_HEAP_PY}",
    f"rm {VI_FILE}; sync",
    "umount /mnt/sd",
)
# The mount table, read with the shell's builtins (umount's own exec can fail with only the
# kernel's report on the console); the card is unmounted when its output lacks CARD_MOUNTED
MOUNTS_PROBE = "exec 3</proc/mounts; while read l <&3; do echo $l; done; exec 3<&-"
CARD_MOUNTED = " /mnt/sd "


def without_kernel_lines(text: str) -> str:
    """text without the kernel's lines (KERNEL_LINE) spliced into it."""
    return KERNEL_LINE.sub("", text)


def missing(out: str, wanted) -> list[str]:
    """What of wanted is not in out."""
    return [w for w in wanted if w not in out]


def pids(ps_out: str, name: str) -> list[int]:
    """The processes busybox's ps lists with that command, its arguments aside. This busybox's
    ps prints `  PID USER       VSZ STAT COMMAND` lines without that header (2026-11)."""
    found = []
    for line in ps_out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].isdigit() and (f[4] == name or f[4].endswith("/" + name)):
            found.append(int(f[0]))
    return found


def cpu_ticks(stat: str) -> int:
    """A process's CPU time so far in clock ticks (utime + stime) from its /proc/<pid>/stat."""
    # The command (field 2) is in parentheses and may hold spaces: count from its end
    f = stat[stat.rindex(")") + 2 :].split()
    return int(f[11]) + int(f[12])


# The memory the userland has, read at the shell with its builtins only (hush's `exec`, `read`
# and `echo`): no exec of a program, so no copy of busybox in RAM while it is measured (noMMU,
# FLAT: every exec copies the whole executable into contiguous pages, docs/agents/linux.md,
# "Memory budget"). The file is opened on fd 3 and each `read` redirected from it: this hush
# drops a `while ...; done < FILE` redirection after the loop's first pass, and the loop then
# reads the console
MEM_PROBES = tuple(
    f"exec 3<{path}; while read l <&3; do echo $l; done; exec 3<&-" for path in ("/proc/meminfo", "/proc/buddyinfo")
)
# The sizes' keys of the card's busyboxes (scripts/make_linux_dist.py's BBTOOLS_PREFIX: their
# names on the card)
BBTOOLS_PREFIX = "bb"
# What the table holds, in its order; the sizes come from make linux-dist's MANIFEST.json
TABLE_ROWS = (
    "Image",
    "initramfs (rootfs.cpio)",
    "busybox",
    "the card's busyboxes",
    "MicroPython (MPY)",
    "MemTotal",
    "MemFree",
    "largest free block",
    "free blocks of order >= 6",
)


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo's `Name: N kB` lines as {Name: N}; other lines (the command's echo) are
    skipped."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].endswith(":") and parts[1].isdigit() and parts[2] == "kB":
            out[parts[0][:-1]] = int(parts[1])
    return out


def parse_buddyinfo(text: str) -> list[int]:
    """/proc/buddyinfo's free blocks per order (index = order), summed over its zones; its
    lines are `Node 0, zone Normal n0 n1 ...`."""
    counts: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "Node" or parts[2] != "zone" or not all(p.isdigit() for p in parts[4:]):
            continue
        for order, n in enumerate(int(p) for p in parts[4:]):
            if order == len(counts):
                counts.append(0)
            counts[order] += n
    return counts


def memory(meminfo: str, buddyinfo: str, sizes: dict[str, int] | None = None) -> dict[str, str]:
    """TABLE_ROWS' values from the probes' output and the build's sizes (bytes), as text;
    raises ValueError when the probes' output lacks what the table needs."""
    info = parse_meminfo(meminfo)
    orders = parse_buddyinfo(buddyinfo)
    lacking = [k for k in ("MemTotal", "MemFree") if k not in info] or ([] if orders else ["buddyinfo"])
    if lacking:
        raise ValueError(f"the probes' output lacks {', '.join(lacking)}")
    largest = max((o for o, n in enumerate(orders) if n), default=None)
    rows = {}
    for name, key in (("Image", "image"), ("initramfs (rootfs.cpio)", "rootfs_cpio"), ("busybox", "busybox")):
        if sizes and key in sizes:
            rows[name] = f"{sizes[key]:,} B"
    # The text tools (make linux-dist's sizes of VUX9K/LINUX/BIN/bb*): on the card, copied into
    # RAM only while one runs, so the total and the largest
    tools = {k: v for k, v in (sizes or {}).items() if k.startswith(BBTOOLS_PREFIX)}
    if tools:
        big = max(tools, key=lambda k: tools[k])
        rows["the card's busyboxes"] = (
            f"{sum(tools.values()):,} B in {len(tools)} (the largest {big}, {tools[big]:,} B)"
        )
    # MPY and its modules' archive (make linux-dist's sizes mpy and pylib_tar): on the card, MPY
    # copied into RAM only while it runs
    if sizes and "mpy" in sizes:
        tar = f", its modules' {PYLIB_TAR} {sizes['pylib_tar']:,} B" if "pylib_tar" in sizes else ""
        rows["MicroPython (MPY)"] = f"{sizes['mpy']:,} B{tar}"
    rows["MemTotal"] = f"{info['MemTotal']:,} kB"
    rows["MemFree"] = f"{info['MemFree']:,} kB"
    rows["largest free block"] = "none" if largest is None else f"{PAGE_KB << largest:,} kB (order {largest})"
    rows["free blocks of order >= 6"] = " ".join(f"{o}:{n}" for o, n in enumerate(orders) if o >= 6)
    return rows
