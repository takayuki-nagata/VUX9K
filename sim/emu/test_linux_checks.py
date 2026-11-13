# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""linux_checks' memory table (sim/integration/linux_checks.py) on the probes' output as the
console gives it: the command's echo first, then the lines hush's `echo` printed. Needs no
Linux build; make test-linux-emu and scripts/hw_linux.py read the real output with it."""

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
    rows = linux_checks.memory(MEMINFO, BUDDYINFO, {"image": 3244488, "rootfs_cpio": 1000, "busybox": 330000})
    assert list(rows) == list(linux_checks.TABLE_ROWS)
    assert rows["MemFree"] == "3,912 kB"
    assert rows["largest free block"] == "1,024 kB (order 8)"
    assert rows["free blocks of order >= 6"] == "6:1 7:0 8:1 9:0 10:0"
    assert rows["Image"] == "3,244,488 B"


def test_table_without_sizes_or_free_blocks():
    rows = linux_checks.memory(MEMINFO, "Node 0, zone Normal 0 0 0 0 0")
    assert "Image" not in rows
    assert rows["largest free block"] == "none"


def test_missing_output_raises():
    with pytest.raises(ValueError):
        linux_checks.memory("MemTotal: 1 kB", BUDDYINFO)
    with pytest.raises(ValueError):
        linux_checks.memory(MEMINFO, "nothing")
