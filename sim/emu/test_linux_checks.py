# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""linux_checks' memory table (sim/integration/linux_checks.py) on the probes' output as the
console gives it: the command's echo first, then the lines hush's `echo` printed; the shape of
its vi sessions and paste. Needs no Linux build; make test-linux-emu and scripts/hw_linux.py
read the real output with it."""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "integration"))
import linux_checks  # noqa: E402

MEMINFO = (
    linux_checks.MEM_PROBES[0]
    + "\r\nMemTotal: 7960 kB\r\nMemFree: 3912 kB\r\nMemAvailable: 3700 kB\r\nBuffers: 0 kB\r\n"
    + "HugePages_Total: 0\r\nVmallocTotal: 0 kB\r\n/ # "
)
BUDDYINFO = linux_checks.MEM_PROBES[1] + "\r\nNode 0, zone Normal 3 1 2 0 4 1 1 0 1 0 0\r\n/ # "


def test_meminfo_skips_echo_and_lines_without_kb():
    info = linux_checks.parse_meminfo(MEMINFO)
    assert info == {"MemTotal": 7960, "MemFree": 3912, "MemAvailable": 3700, "Buffers": 0, "VmallocTotal": 0}


def test_buddyinfo_sums_zones():
    two = "Node 0, zone DMA 1 0 1\nNode 0, zone Normal 2 1 0 1\n"
    assert linux_checks.parse_buddyinfo(two) == [3, 1, 1, 1]
    assert linux_checks.parse_buddyinfo(BUDDYINFO) == [3, 1, 2, 0, 4, 1, 1, 0, 1, 0, 0]


def test_table():
    sizes = {
        "image": 3244488,
        "rootfs_cpio": 1000,
        "busybox": 330000,
        "bbedit": 87144,
        "bbawk": 133172,
        "mpy": 492500,
        "pylib_tar": 40960,
    }
    rows = linux_checks.memory(MEMINFO, BUDDYINFO, sizes)
    assert list(rows) == list(linux_checks.TABLE_ROWS)
    assert rows["the card's busyboxes"] == "220,316 B in 2 (the largest bbawk, 133,172 B)"
    assert rows["MicroPython (MPY)"] == "492,500 B, its modules' VUX9K/LINUX/PYLIB.TAR 40,960 B"
    assert rows["MemFree"] == "3,912 kB"
    assert rows["largest free block"] == "1,024 kB (order 8)"
    assert rows["free blocks of order >= 6"] == "6:1 7:0 8:1 9:0 10:0"
    assert rows["Image"] == "3,244,488 B"


def test_table_without_sizes_or_free_blocks():
    rows = linux_checks.memory(MEMINFO, "Node 0, zone Normal 0 0 0 0 0")
    assert "Image" not in rows and "the card's busyboxes" not in rows and "MicroPython (MPY)" not in rows
    assert rows["largest free block"] == "none"


def test_commands_are_each_ones():
    """COMMANDS joins its parts' dicts: a command in two of them would run once, at the first's
    place, with the second's expectations."""
    parts = (
        linux_checks.BASE_COMMANDS,
        linux_checks.MPY_COMMANDS,
        linux_checks.LED_COMMANDS,
        linux_checks.TOOL_COMMANDS,
        linux_checks.PIPE_COMMANDS,
        linux_checks.SCRIPT_COMMANDS,
    )
    assert len(linux_checks.COMMANDS) == sum(len(p) for p in parts)


def test_board_lines_fit_the_console():
    """What the board's test types at the shell is shorter than the console's 80 columns with
    the prompt (a longer line wraps in hush's line editor, which then echoes more than was
    typed, and send_line waits for each chunk's echo)."""
    lines = (
        *linux_checks.COMMANDS,
        *linux_checks.CARD_CLEANUP,
        *linux_checks.MEM_PROBES,
        linux_checks.MOUNTS_PROBE,
        linux_checks.VI_CLEAR,
        linux_checks.MPY_REPL,
        linux_checks.MPY_REPL_LINE,
    )
    for line in lines:
        assert len(line) + len(linux_checks.PROMPT) < 80, line


def test_tool_busyboxes_are_the_builds():
    """TOOL_BUSYBOXES is the card's busyboxes (the package's VUX9K_BBTOOLS_ORDERS), each applet
    in its fragment (CONFIG_<APPLET>=y), and TOOL_COMMANDS runs each of them, so the board's
    test times every one's first exec."""
    bbtools = os.path.join(REPO_ROOT, "linux_workspace", "userland", "bbtools")
    mk = os.path.join(REPO_ROOT, "linux_workspace", "buildroot", "package", "vux9k-bbtools", "vux9k-bbtools.mk")
    with open(mk) as f:
        orders = next(ln for ln in f if ln.startswith("VUX9K_BBTOOLS_ORDERS = "))
    names = {t.split(":")[0] for t in orders.split("=", 1)[1].split()}
    assert set(linux_checks.TOOL_BUSYBOXES) == names
    for bb, applets in linux_checks.TOOL_BUSYBOXES.items():
        with open(os.path.join(bbtools, f"{bb}.config")) as f:
            config = {ln.strip() for ln in f}
        for applet in applets:
            assert f"CONFIG_{applet.upper()}=y" in config, (bb, applet)
    assert set(linux_checks.first_execs(linux_checks.TOOL_COMMANDS)) == names
    # The first of a busybox's commands, not a later one
    assert linux_checks.first_execs(["ls x", "uniq b", "sort a", "grep c"]) == {"bbsort": "uniq b", "bbgrep": "grep c"}


def test_missing_output_raises():
    with pytest.raises(ValueError):
        linux_checks.memory("MemTotal: 1 kB", BUDDYINFO)
    with pytest.raises(ValueError):
        linux_checks.memory(MEMINFO, "nothing")


def test_pids_from_busybox_ps():
    # As busybox's ps prints it, with no header (2026-11, the emulator)
    out = (
        "ps\r\n    1 root        24 S    /init\r\n    2 root         0 SW   [kthreadd]\r\n"
        "   21 root         0 IW<  [kworker/0:1H-kb]\r\n   22 root        24 S    sysmon\r\n"
        "   52 root        24 S    /usr/sbin/sysmon -v\r\n   23 root       316 S    -sh\r\n"
        "   33 root       312 R    ps\r\n/ # "
    )
    assert linux_checks.pids(out, "sysmon") == [22, 52]
    assert linux_checks.pids(out, "init") == [1]
    assert linux_checks.pids(out, "sh") == []
    assert linux_checks.pids(out, "kthreadd") == []


def test_cpu_ticks_counts_user_and_system_time():
    assert linux_checks.cpu_ticks("47 (sysmon) S 1 47 47 0 -1 4194560 10 0 0 0 7 3 0 0 20 0 1 0 100") == 10
    # A command with spaces and parentheses in it
    assert linux_checks.cpu_ticks("9 (a (b) c) R 1 9 9 0 -1 0 0 0 0 0 1 2 0 0 20 0 1 0 5") == 3


def test_vi_sessions_fit_the_fifo_and_start_new():
    """Each vi step's part fits the UART's RX FIFO (32 bytes) and is a whole key sequence or
    text; the first session makes the file (VI_CLEAR before it) and the others edit it."""
    for session in linux_checks.VI_SESSIONS:
        for step in session.steps:
            for part in (step,) if isinstance(step, str) else step:
                assert 0 < len(part) < 32, (session.name, part)
        assert session.text.endswith("\n") and "\r" not in session.text
    first = linux_checks.VI_SESSIONS[0].steps[0]
    assert isinstance(first, str) and first.startswith("i")
    assert linux_checks.VI_SPLIT_S < linux_checks.VI_ESC_WAIT_S < linux_checks.VI_QUIET_S


def test_paste_lines_hide_their_output():
    """A paste line's echo doesn't hold its output (the word), so the words in the console are
    the lines that ran; ~200 bytes, each line shorter than the console's 80 columns with the
    prompt."""
    assert len(linux_checks.PASTE_LINES) == len(linux_checks.PASTE_WORDS)
    for line, word in zip(linux_checks.PASTE_LINES, linux_checks.PASTE_WORDS, strict=True):
        assert word not in line and "\\" in line
        assert len(line) + len(linux_checks.PROMPT) < 80
    assert linux_checks.PASTE_DONE_OUT not in linux_checks.PASTE_DONE
    assert 180 <= sum(len(line) + 1 for line in linux_checks.PASTE_LINES) <= 256
    for line in linux_checks.SCRIPT_COMMANDS:
        assert len(line) + len(linux_checks.PROMPT) < 80, line


def test_kernel_lines():
    """Only a line with printk's timestamp goes, wherever it splits the text; a command's own
    brackets and numbers stay."""
    kernel = "[  255.548990] random: crng init done\r\n"
    assert linux_checks.without_kernel_lines(f"exec 3</proc/b{kernel}uddyinfo") == "exec 3</proc/buddyinfo"
    assert linux_checks.without_kernel_lines(f"[    0.000000] Linux version 7.3\n{kernel}") == ""
    kept = "[1] 83\r\n[ 1.5] x\r\n[2.123456]x\r\n1.000000] y\r\n"
    assert linux_checks.without_kernel_lines(kept) == kept


def test_mpy_commands():
    """Which commands execute MPY (each waits for a block of MPY_ORDER first): its name as a
    word, also after a subshell's parenthesis, not in a path or a script's text; every one of
    them is checked for MPY_UNWANTED; the scripts are written before they run."""
    assert linux_checks.runs_mpy('MPY -c "print(1)"')
    assert linux_checks.runs_mpy('(unset MICROPYPATH; exec MPY -c "x")')
    assert linux_checks.runs_mpy(f"MPY {linux_checks.MPY_HEAP_PY}")
    assert not linux_checks.runs_mpy(f'echo "import sys" > {linux_checks.MPY_ARGS_PY}')
    assert not linux_checks.runs_mpy("echo $MICROPYPATH")
    runs = [c for c in linux_checks.MPY_COMMANDS if linux_checks.runs_mpy(c)]
    assert len(runs) == 6 and runs[0].startswith("MPY -c ")
    assert linux_checks.problems(runs[0], "Traceback (most recent call last):") != []
    assert linux_checks.problems("ls /", "Traceback") == []
    seen: set[str] = set()
    for cmd in linux_checks.MPY_COMMANDS:
        for path in (linux_checks.MPY_ARGS_PY, linux_checks.MPY_LED_PY, linux_checks.MPY_HEAP_PY):
            if cmd.startswith("echo ") and cmd.endswith(f"> {path}"):
                assert (path in seen) == cmd.endswith(f">> {path}"), cmd
                seen.add(path)
            elif path in cmd:
                assert path in seen, cmd
    # Every file the commands write on the card goes again
    cleanup = " ".join(linux_checks.CARD_CLEANUP)
    assert all(p in cleanup for p in seen)


def test_heap_problems():
    """The heap must grow to MPY_HEAP_BYTES in steps of at most HEAP_STEP_MAX."""
    heap = linux_checks.heap_problems

    def info(*totals):
        return "".join(f"GC: total: {t}, used: 1, free: 2, max new split: 126976\r\n" for t in totals)

    assert heap(info(63_936, 63_936, 128_000, 128_000, 250_368, 250_368)) == []
    assert heap("heap 200") != []
    # Never grown, or not enough
    assert heap(info(63_936, 63_936)) != []
    assert heap(info(63_936, 128_000)) != []
    # A step of more than an order-5 block (the cap's absence: the heap doubles)
    assert heap(info(63_936, 128_000, 260_000)) != []
