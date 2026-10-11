# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Transfer v2 on both sides: tools/vux_tool.py's sender and fw_common's upload_v2 (device).

firmware/fw_common/tests/upload_v2_vectors/ holds transcripts: the device's lines and the
host's bytes in the order they cross the UART, with the device's byte limit and which sink
call fails. fw_common's host test (tests/host.rs) replays each transcript's host bytes into
upload_v2 and checks its lines; this test checks that send_upload_v2, talking to the
transcript's device lines, writes exactly its host bytes. The transcripts come from a small
reference device below (VUX9_VECTORS_UPDATE=1 regenerates them), so a disagreement between
the Rust device and this one shows up as a failing Rust test.
"""

import binascii
import os
import struct
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

VECTOR_DIR = os.path.join(REPO_ROOT, "firmware", "fw_common", "tests", "upload_v2_vectors")
UPDATE = os.environ.get("VUX9_VECTORS_UPDATE") == "1"
MAX = 2048


def _data(n):
    return bytes((i * 7 + 3) & 0xFF for i in range(n))


def _reference_device(host, max_len, fail):
    """[("dev", line) | ("host", bytes)] for a device that receives `host` (fw_common's
    upload_v2, written again from its doc comment). Host bytes are cut where the device
    reads them; bytes it never reads are left out."""
    events = []
    pos = 0

    def recv(n):
        nonlocal pos
        got = host[pos : pos + n]
        pos += len(got)
        if got:
            events.append(("host", got))
        return got if len(got) == n else None

    def dev(line):
        events.append(("dev", line))

    dev("[READY-V2]")
    raw = recv(4)
    if raw is None:
        dev("[ERR] Length timeout")
        return events
    (length,) = struct.unpack("<I", raw)
    if length == 0 or length > max_len:
        dev(f"[ERR] Bad length: {length}")
        return events
    if fail == "begin":
        dev(f"[ERR] Cannot store {length} bytes")
        return events
    dev(f"[LEN:{length}]")
    data = b""
    for index, at in enumerate(range(0, length, vux_tool.UPLOAD_V2_BLOCK)):
        n = min(vux_tool.UPLOAD_V2_BLOCK, length - at)
        dev(f"[READY-BLK:{index}]")
        got = recv(n)
        if got is None:
            dev(f"[ERR] Timeout at block {index}, byte {len(events[-1][1]) if events[-1][0] == 'host' else 0}")
            return events
        if fail == f"block:{index}":
            dev(f"[ERR] Store failed at block {index}")
            return events
        data += got
    dev("[READY-CRC]")
    raw = recv(4)
    if raw is None:
        dev("[ERR] CRC timeout")
        return events
    (sent,) = struct.unpack("<I", raw)
    crc = binascii.crc32(data) & 0xFFFFFFFF
    if sent != crc:
        dev(f"[ERR] CRC mismatch: sent {sent:08X}, received {crc:08X}")
        return events
    if fail == "finish":
        dev("[ERR] Commit failed")
        return events
    dev(f"[OK] {length} bytes CRC {crc:08X}")
    return events


def _host_bytes(data, crc_override=None):
    """What a sender that gets every [READY...] line writes for data."""
    crc = crc_override if crc_override is not None else binascii.crc32(data) & 0xFFFFFFFF
    return struct.pack("<I", len(data)) + data + struct.pack("<I", crc)


# case -> (host bytes, max_len, failing sink call, whether send_upload_v2 makes these bytes,
# the data and crc_override it is given). Cases a sender never produces (it stops sending)
# are device-only.
CASES = {
    "one_byte": (_data(1), MAX, "none", None),
    "len_10": (_data(10), MAX, "none", None),
    "len_13": (_data(13), MAX, "none", None),
    "one_block": (_data(512), MAX, "none", None),
    "block_and_one": (_data(513), MAX, "none", None),
    "at_limit": (_data(MAX), MAX, "none", None),
    "over_limit": (_data(MAX + 1), MAX, "none", None),
    "empty": (b"", MAX, "none", None),
    "sink_refuses": (_data(100), MAX, "begin", None),
    "store_fails": (_data(1100), MAX, "block:1", None),
    "commit_fails": (_data(100), MAX, "finish", None),
    "bad_crc": (_data(600), MAX, "none", 0x12345678),
}
# Device-only cases: host bytes that stop short.
TIMEOUTS = {
    "length_timeout": struct.pack("<I", 600)[:3],
    "block_timeout": struct.pack("<I", 600) + _data(517),
    "crc_timeout": struct.pack("<I", 3) + _data(3) + b"\x01\x02",
}


def _transcript(case):
    if case in CASES:
        data, max_len, fail, crc = CASES[case]
        host, sender = _host_bytes(data, crc), True
    else:
        host, max_len, fail, sender = TIMEOUTS[case], MAX, "none", False
    lines = [
        "# sim/emu/test_upload_v2.py (VUX9_VECTORS_UPDATE=1 regenerates)",
        f"max {max_len}",
        f"fail {fail}",
        f"sender {'yes' if sender else 'no'}",
    ]
    for kind, value in _reference_device(host, max_len, fail):
        lines.append(f"dev {value}" if kind == "dev" else f"host {value.hex()}")
    return "\n".join(lines) + "\n"


ALL = sorted([*CASES, *TIMEOUTS])


@pytest.mark.parametrize("case", ALL)
def test_transcripts_match_reference_device(case):
    path = os.path.join(VECTOR_DIR, f"{case}.txt")
    text = _transcript(case)
    if UPDATE:
        os.makedirs(VECTOR_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    with open(path) as f:
        assert f.read() == text, f"{case}.txt differs (VUX9_VECTORS_UPDATE=1 regenerates)"


def test_no_stale_transcripts():
    assert sorted(os.path.splitext(n)[0] for n in os.listdir(VECTOR_DIR)) == ALL


class TranscriptSerial:
    """The device of a transcript: read_until hands out its lines while the next event is
    the device's; write must match the host bytes that come next."""

    def __init__(self, events):
        self.events = list(events)
        self.written = b""

    def read_until(self, expected=b"\n"):
        if not self.events or self.events[0][0] != "dev":
            raise AssertionError(f"the sender waits for a line the device does not send; next: {self.events[:1]}")
        return self.events.pop(0)[1].encode() + b"\n"

    def write(self, data):
        while data:
            assert self.events and self.events[0][0] == "host", f"unexpected write {data[:16]!r}"
            want = self.events[0][1]
            n = min(len(want), len(data))
            assert data[:n] == want[:n], "the sender's bytes differ from the transcript's"
            self.events[0] = ("host", want[n:])
            if not self.events[0][1]:
                self.events.pop(0)
            data = data[n:]

    def flush(self):
        pass


def _events(case):
    with open(os.path.join(VECTOR_DIR, f"{case}.txt")) as f:
        rows = [line.rstrip("\n").split(" ", 1) for line in f if not line.startswith("#")]
    return [(k, v if k == "dev" else bytes.fromhex(v)) for k, v in rows if k in ("dev", "host")]


@pytest.mark.parametrize("case", sorted(CASES))
def test_sender_follows_transcript(case):
    data, _, _, crc = CASES[case]
    events = _events(case)
    ser = TranscriptSerial(events)
    last = [v for k, v in events if k == "dev"][-1]
    if last.startswith("[OK]"):
        assert vux_tool.send_upload_v2(ser, data, crc_override=crc) == last
    else:
        with pytest.raises(vux_tool.UploadError, match=r"^\[ERR\]"):
            vux_tool.send_upload_v2(ser, data, crc_override=crc)
    assert not ser.events, f"transcript not used up: {ser.events[:1]}"


def test_sender_skips_other_lines_and_reports_progress():
    events = _events("block_and_one")
    events.insert(0, ("dev", "u"))
    events.insert(1, ("dev", "S1: upload"))
    seen = []
    ser = TranscriptSerial(events)
    vux_tool.send_upload_v2(ser, _data(513), progress_cb=lambda i, n: seen.append((i, n)))
    assert seen == [(0, 2), (1, 2)]


def test_sender_times_out():
    class Silent:
        def read_until(self, expected=b"\n"):
            return b"[REA"

    with pytest.raises(vux_tool.UploadError, match="no answer"):
        vux_tool.send_upload_v2(Silent(), b"x", timeout=0.01)
