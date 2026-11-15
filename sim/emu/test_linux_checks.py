# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""linux_checks' memory table (sim/integration/linux_checks.py) on the probes' output as the
console gives it: the command's echo first, then the lines hush's `echo` printed; the shape of
its vi sessions and paste. Needs no Linux build; make test-linux-emu and scripts/hw_linux.py
read the real output with it."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integration"))
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
    sizes = {"image": 3244488, "rootfs_cpio": 1000, "busybox": 330000, "bbedit": 87144, "bbawk": 133172}
    rows = linux_checks.memory(MEMINFO, BUDDYINFO, sizes)
    assert list(rows) == list(linux_checks.TABLE_ROWS)
    assert rows["the card's busyboxes"] == "220,316 B in 2 (the largest bbawk, 133,172 B)"
    assert rows["MemFree"] == "3,912 kB"
    assert rows["largest free block"] == "1,024 kB (order 8)"
    assert rows["free blocks of order >= 6"] == "6:1 7:0 8:1 9:0 10:0"
    assert rows["Image"] == "3,244,488 B"


def test_table_without_sizes_or_free_blocks():
    rows = linux_checks.memory(MEMINFO, "Node 0, zone Normal 0 0 0 0 0")
    assert "Image" not in rows and "the card's busyboxes" not in rows
    assert rows["largest free block"] == "none"


def test_commands_are_each_ones():
    """COMMANDS joins its parts' dicts: a command in two of them would run once, at the first's
    place, with the second's expectations."""
    parts = (
        linux_checks.BASE_COMMANDS,
        linux_checks.LED_COMMANDS,
        linux_checks.TOOL_COMMANDS,
        linux_checks.PIPE_COMMANDS,
    )
    assert len(linux_checks.COMMANDS) == sum(len(p) for p in parts)


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
