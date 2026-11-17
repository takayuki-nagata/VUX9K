# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""tools/vux_tool.py's monitor (no board, no emulator): what the host types or pastes goes to
the board PASTE_CHUNK bytes at a time, PASTE_GAP_MS apart (the board's 32-byte RX FIFO, which
Linux's liteuart driver empties every 10 ms: a paste sent back to back loses bytes, which is
how the SoC is; sim/emu/test_linux.py's test_paste wants that as its control, and none lost with
this pacing), a key at once; the board's bare LFs reach
the terminal as CR LF, and vi's screen (cursor addressing, no bare LF) as it was sent. The
monitor itself runs on a pseudo-terminal for stdin and a socket for the port."""

import argparse
import os
import pty
import socket
import sys
import termios
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.vux_tool as vux_tool  # noqa: E402

GAP = vux_tool.PASTE_GAP_MS / 1000
# Wall-clock seconds the monitor's own test may take before its keys end it anyway
MONITOR_TIMEOUT_S = 10


def drain(pacer: vux_tool.Pacer, now: float) -> list[tuple[float, bytes]]:
    """Every chunk the pacer gives from `now` on, with when it went, the clock moved to each
    chunk's time as the monitor's select does."""
    out: list[tuple[float, bytes]] = []
    for _ in range(10_000):  # a step cap: a pacer that never empties fails, not hangs
        wait = pacer.wait(now)
        if wait is None:
            return out
        now += wait
        chunk = pacer.pop(now)
        assert chunk, "wait() said now, pop() gave nothing"
        out.append((now, chunk))
    raise AssertionError("the pacer never emptied")


def test_paste_goes_in_chunks_apart():
    pacer = vux_tool.Pacer()
    data = bytes(range(200))
    pacer.push(data)
    sent = drain(pacer, 100.0)
    assert b"".join(c for _, c in sent) == data
    assert all(len(c) == vux_tool.PASTE_CHUNK for _, c in sent[:-1]) and 0 < len(sent[-1][1]) <= vux_tool.PASTE_CHUNK
    times = [t for t, _ in sent]
    assert times[0] == 100.0, "the first chunk waits for nothing"
    assert all(b - a == pytest.approx(GAP) for a, b in zip(times, times[1:], strict=False)), times


def test_a_key_goes_at_once_and_an_escape_sequence_whole():
    pacer = vux_tool.Pacer()
    assert pacer.wait(0.0) is None and pacer.pop(0.0) == b""
    pacer.push(b"\x1b[A")  # an arrow key: one read of the terminal
    assert pacer.pop(5.0) == b"\x1b[A"
    # The next key within the gap waits for it, then goes
    pacer.push(b"k")
    assert pacer.wait(5.0 + GAP / 2) == pytest.approx(GAP / 2)
    assert pacer.pop(5.0 + GAP / 2) == b""
    assert pacer.pop(5.0 + GAP) == b"k"
    # Typed later than the gap: at once
    pacer.push(b"j")
    assert pacer.wait(9.0) == 0.0 and pacer.pop(9.0) == b"j"


@pytest.mark.parametrize("chunk, gap", [(0, GAP), (16, 0.0)])
def test_no_pacing(chunk, gap):
    pacer = vux_tool.Pacer(chunk, gap)
    pacer.push(bytes(100))
    pacer.pop(0.0)
    pacer.push(bytes(300))
    assert drain(pacer, 0.0) == [(0.0, bytes(300))]


def test_monitor_output_turns_bare_lfs_only_into_crlf():
    # The stages print a bare LF; Linux's tty CR LF already
    assert vux_tool.monitor_output(b"s2> ls\nVUX9K\n") == b"s2> ls\r\nVUX9K\r\n"
    assert vux_tool.monitor_output(b"/ # ls\r\nbin\r\n") == b"/ # ls\r\nbin\r\n"
    # vi's screen: cursor addressing, CR, erase to the end of the line; nothing to change
    screen = b"\x1b[1;1H\x1b[Jone\x1b[K\x1b[2;1Hthree\r\x1b[24;1H\x1b[K- /mnt/sd/X 1/2 50%\x1b[1;1H"
    assert vux_tool.monitor_output(screen) == screen


class FakePort:
    """A pyserial-like port on one end of a socket pair (the board's end is the other): what
    the monitor writes, each write with its time."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.writes: list[tuple[float, bytes]] = []
        sock.setblocking(False)

    def fileno(self) -> int:
        return self.sock.fileno()

    in_waiting = 0

    def write(self, data: bytes) -> int:
        self.writes.append((time.monotonic(), bytes(data)))
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int = 1) -> bytes:
        try:
            return self.sock.recv(size)
        except BlockingIOError:
            return b""

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.sock.close()


def test_monitor_paces_a_paste(monkeypatch, tmp_path):
    """The monitor on a pseudo-terminal: a 200-byte paste reaches the port in chunks of at
    most PASTE_CHUNK, PASTE_GAP_MS apart, in order; the board's output reaches the terminal
    with its bare LFs as CR LF; Ctrl+C goes to the board and Ctrl+] ends it. The gaps are
    checked on the times the monitor gave its pacer, not on when the writes arrived: a loaded
    machine can't shorten them."""
    board, host = socket.socketpair()
    port = FakePort(host)
    monkeypatch.setattr(vux_tool, "open_port", lambda *a, **kw: port)
    popped: list[tuple[float, bytes]] = []

    class Recorded(vux_tool.Pacer):
        """The monitor's pacer, with the time and bytes of each chunk it gave"""

        def pop(self, now):
            out = super().pop(now)
            if out:
                popped.append((now, out))
            return out

    monkeypatch.setattr(vux_tool, "Pacer", Recorded)
    master, slave = pty.openpty()
    monkeypatch.setattr(sys, "stdin", os.fdopen(slave, "rb", buffering=0))
    out_path = tmp_path / "terminal"
    monkeypatch.setattr(sys, "stdout", open(out_path, "w"))
    paste = b"".join(b"echo paste-%02d-abcdefghij\r" % n for n in range(8))

    def keys():
        end = time.monotonic() + MONITOR_TIMEOUT_S
        # Once the monitor has the terminal raw: tty.setraw drops the input typed before
        while termios.tcgetattr(master)[3] & termios.ICANON and time.monotonic() < end:
            time.sleep(0.01)
        os.write(master, paste)
        board.sendall(b"s2> ls\nVUX9K\n")
        while sum(len(d) for _, d in port.writes[1:]) < len(paste) and time.monotonic() < end:
            time.sleep(0.01)
        time.sleep(0.2)  # the board's output read too
        # Ctrl+C goes to the board (it stops a program there), only Ctrl+] ends the monitor
        os.write(master, b"\x03")
        while sum(len(d) for _, d in port.writes[1:]) < len(paste) + 1 and time.monotonic() < end:
            time.sleep(0.01)
        os.write(master, b"\x1d")

    t = threading.Thread(target=keys, daemon=True)
    t.start()
    try:
        args = argparse.Namespace(
            port="fake", baud=115200, paste_chunk=vux_tool.PASTE_CHUNK, paste_gap_ms=vux_tool.PASTE_GAP_MS
        )
        vux_tool.cmd_monitor(args)
    finally:
        sys.stdout.close()
        sys.stdin.close()
        t.join(MONITOR_TIMEOUT_S)
        os.close(master)
        board.close()
    # The first write is the monitor's own CR for a fresh prompt
    assert port.writes[0][1] == b"\r"
    sent = port.writes[1:]
    assert b"".join(d for _, d in sent) == paste + b"\x03"
    assert all(len(d) <= vux_tool.PASTE_CHUNK for _, d in sent), [len(d) for _, d in sent]
    # Each chunk as the pacer gave it, GAP apart on the monitor's clock (less a rounding)
    assert [d for _, d in popped] == [d for _, d in sent]
    times = [t for t, _ in popped]
    assert all(b - a >= GAP - 1e-9 for a, b in zip(times, times[1:], strict=False)), times
    assert b"s2> ls\r\nVUX9K\r\n" in out_path.read_bytes()
