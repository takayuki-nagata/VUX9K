# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side handling of the board's USB-UART bridge (no board, no emulator).

The bridge (a BL702 that emulates an FT2232) keeps one line setting for both interfaces:
SET_BAUD only stores a rate, SET_DATA applies the stored one, and Linux's ftdi_sio sends
SET_DATA before SET_BAUD on open. After ModemManager probed the JTAG tty at 57600 (it does
after each openFPGALoader load), the next session on the UART tty ran at 57600 and read
the board's 115200 output as noise. Found on the board 2026-10; these tests pin the
tools' side: re-applying the line settings after open, the ModemManager check, the
garbled-output note, and hw_smoke ignoring output from before the load.
"""

import os
import sys

import pytest
import serial

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hw_smoke  # noqa: E402

import tools.vux_tool as vux_tool  # noqa: E402

# hw_test's "RESULT PASS 0B/0B 00000000\n" as the board's host read it with the bridge at 57600
RESULT_AT_57600 = bytes.fromhex("befeedc8dc0e8a490c4a4a4a4af8")


def test_open_port_sets_the_line_format_twice(monkeypatch):
    """After the open, the line format is set twice: the bridge then runs at the port's own rate."""
    seen = []
    real = serial.Serial._reconfigure_port

    def record(self, *a, **k):
        seen.append(self.stopbits)
        return real(self, *a, **k)

    monkeypatch.setattr(serial.Serial, "_reconfigure_port", record)
    master, slave = os.openpty()
    try:
        ser = vux_tool.open_port(os.ttyname(slave), baudrate=115200, timeout=0.05)
        try:
            assert seen == [serial.STOPBITS_ONE, serial.STOPBITS_TWO, serial.STOPBITS_ONE]
            assert ser.stopbits == serial.STOPBITS_ONE and ser.baudrate == 115200
        finally:
            ser.close()
    finally:
        os.close(master)
        os.close(slave)


def test_apply_line_settings_leaves_non_tty_ports_alone():
    class Socketish:
        stopbits = 1

    s = Socketish()
    vux_tool.apply_line_settings(s)  # no tcsetattr on the emulator's socket
    assert s.stopbits == 1


@pytest.mark.parametrize(
    "data, expected",
    [
        (RESULT_AT_57600, True),
        (RESULT_AT_57600.decode("utf-8", errors="replace"), True),
        (b"\nvux> ", False),
        ("  POS (AU)  :  X = +0.7198389\x1b[K\r\n", False),  # the Zephyr demo's escapes
    ],
)
def test_garbled(data, expected):
    assert vux_tool.garbled(data) is expected
    assert bool(vux_tool.garbled_hint(data)) is expected


def make_host(tmp_path, mm_running, ignored):
    """A fake /sys, /run and /proc: the board's bridge with if00 -> ttyUSB2, if01 -> ttyUSB3."""
    sysfs, run, proc = tmp_path / "sys", tmp_path / "run", tmp_path / "proc"
    usb = sysfs / "devices" / "usb1" / "1-3" / "1-3.3"
    (usb).mkdir(parents=True)
    (usb / "idVendor").write_text("0403\n")
    for intf, tty in (("1-3.3:1.0", "ttyUSB2"), ("1-3.3:1.1", "ttyUSB3")):
        (usb / intf / tty / "tty" / tty).mkdir(parents=True)  # as ftdi_sio lays them out
        cls = sysfs / "class" / "tty" / tty
        cls.mkdir(parents=True)
        (cls / "device").symlink_to(usb / intf / tty)
        minor = tty[-1]
        (cls / "dev").write_text(f"188:{minor}\n")
        data = run / "udev" / "data"
        data.mkdir(parents=True, exist_ok=True)
        props = "E:ID_MM_CANDIDATE=1\n" + ("E:ID_MM_DEVICE_IGNORE=1\n" if tty in ignored else "")
        (data / f"c188:{minor}").write_text(props)
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "comm").write_text("systemd\n")
    if mm_running:
        (proc / "1009").mkdir()
        (proc / "1009" / "comm").write_text("ModemManager\n")
    return dict(sysfs=str(sysfs), run=str(run), proc=str(proc))


@pytest.mark.parametrize(
    "mm_running, ignored, probed",
    [
        (False, (), None),
        (True, (), "ttyUSB2, ttyUSB3"),
        (True, ("ttyUSB3",), "ttyUSB2"),  # the JTAG tty is the one it opens after a load
        (True, ("ttyUSB2", "ttyUSB3"), None),
    ],
)
def test_modemmanager_hint(tmp_path, mm_running, ignored, probed):
    paths = make_host(tmp_path, mm_running, ignored)
    hint = vux_tool.modemmanager_hint("/dev/ttyUSB3", **paths)
    if probed is None:
        assert hint == ""
    else:
        assert f"probe the board's {probed}:" in hint and "70-vux9k-board.rules" in hint


def test_modemmanager_hint_ignores_non_usb_ports(tmp_path):
    paths = make_host(tmp_path, True, ())
    assert vux_tool.modemmanager_hint("/dev/pts/7", **paths) == ""


STALE = b"RESULT PASS 0B/0B 00000000\nRESULT PASS 0B/0B 00000000\n  [r] Reboot SoC via Resident Loader\nvux> "
FRESH = b"\nhw_test\nT01 add C02B9A30 FAIL\nRESULT FAIL 00/0B 00000001\n"


def test_hw_smoke_needs_this_boots_banner():
    # the bridge delivers the previous seed's lines first: before the fix, the first RESULT won
    first = hw_smoke.RESULT.search(STALE + FRESH)
    assert first is not None and first.group(1) == b"PASS"
    assert hw_smoke.hw_test_result(STALE) is None
    m = hw_smoke.hw_test_result(STALE + FRESH)
    assert m is not None and m.group(1) == b"FAIL"


def test_hw_smoke_drops_what_came_before_the_load(monkeypatch):
    loaded: list[list[str]] = []

    class Board:
        def read(self, n):
            # the bridge hands over held output right after the open, the new boot after the load
            if not loaded:
                out, self.stale = getattr(self, "stale", STALE), b""
                return out
            out, self.fresh = getattr(self, "fresh", FRESH), b""
            return out

        def close(self):
            pass

    monkeypatch.setattr(vux_tool, "open_port", lambda *a, **k: Board())
    monkeypatch.setattr(hw_smoke.subprocess, "run", lambda cmd, **k: loaded.append(cmd))
    out = hw_smoke.run_on_board("seed_2.fs", "auto", timeout=2.0)
    assert loaded and out == FRESH
    m = hw_smoke.hw_test_result(out)
    assert m is not None and m.group(1) == b"FAIL"
