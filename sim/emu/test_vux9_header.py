# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The VUX9 v4 header on both sides: tools/vux_tool.py (writes files) and fw_common (reads them).

firmware/fw_common/tests/vux9_v4_vectors/ holds headers vux_tool made, each with the fields
fw_common's ImageHeader::parse must return (`<case>.txt`, `field value` lines, or `invalid`).
This test checks the files are what vux_tool makes today and that vux_tool's own parser
agrees; fw_common's host test (tests/host.rs) checks the firmware's parser against the same
files. Regenerate after a deliberate header change with VUX9_VECTORS_UPDATE=1.
"""

import dataclasses
import os
import re
import struct
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

V4_VECTOR_DIR = os.path.join(REPO_ROOT, "firmware", "fw_common", "tests", "vux9_v4_vectors")
HEADER_RS = os.path.join(REPO_ROOT, "firmware", "fw_common", "src", "header.rs")
MAP_RS = os.path.join(REPO_ROOT, "firmware", "fw_common", "src", "map.rs")
UPDATE = os.environ.get("VUX9_VECTORS_UPDATE") == "1"

NOPS = bytes.fromhex("13000000") * 4
V4_FIELDS = ("mode", "size", "load_addr", "entry_point", "crc32", "version", "name")


def _image_v4(**kw):
    raw, _ = vux_tool.build_vux9_v4_image(kw.pop("payload", NOPS), **kw)
    return bytearray(raw[: vux_tool.HEADER_LEN])


def _patched(base, offset, fmt, value):
    struct.pack_into(fmt, base, offset, value)
    return base


# case -> 64-byte header. The invalid ones are changed after packing, since vux_tool
# writes only valid headers (except for the magic, which tests override).
V4_CASES = {
    "stage2": lambda: _image_v4(name="Stage2", version=2, load_addr=0x80700000, entry_point=0x80700000),
    "hack_app": lambda: _image_v4(name="Hk", mode="hack", version=1),
    "riscv_loaded": lambda: _image_v4(
        payload=bytes(range(256)) * 3,
        name="N" * 32,
        version=0xFFFFFFFF,
        load_addr=0x80001000,
        entry_point=0x80001004,
    ),
    "bad_magic": lambda: _image_v4(name="x", magic_override=0x56555838),
    "bad_header_version": lambda: _patched(_image_v4(name="x"), 4, "<H", 3),
    "reserved_set": lambda: _patched(_image_v4(name="x"), 6, "<H", 1),
}


def _expected_v4(header):
    h = vux_tool.ImageHeader.parse(bytes(header))
    if h is None:
        return "invalid\n"
    values = {f: getattr(h, f) for f in V4_FIELDS}
    values["name"] = h.name.hex()
    return "".join(f"{f} {values[f]}\n" for f in V4_FIELDS)


@pytest.mark.parametrize("case", sorted(V4_CASES))
def test_vectors_match_vux_tool(case):
    vector_dir = V4_VECTOR_DIR
    header = bytes(V4_CASES[case]())
    expected = _expected_v4(header)
    bin_path = os.path.join(vector_dir, f"{case}.bin")
    txt_path = os.path.join(vector_dir, f"{case}.txt")
    if UPDATE:
        os.makedirs(vector_dir, exist_ok=True)
        with open(bin_path, "wb") as f:
            f.write(header)
        with open(txt_path, "w") as f:
            f.write(expected)
    with open(bin_path, "rb") as f:
        assert f.read() == header, f"{case}.bin differs from vux_tool's (VUX9_VECTORS_UPDATE=1 regenerates)"
    with open(txt_path) as f:
        assert f.read() == expected, f"{case}.txt differs from vux_tool's parse"


def test_no_stale_vectors():
    files = {os.path.splitext(n)[0] for n in os.listdir(V4_VECTOR_DIR)}
    assert files == set(V4_CASES), "vectors without a case (or the reverse); remove or regenerate"


def test_v4_pack_parse_round_trip():
    h = vux_tool.ImageHeader(mode=vux_tool.MODE_HACK, size=5, crc32=7, name=b"app", version=3, load_addr=8)
    assert vux_tool.ImageHeader.parse(h.pack()) == dataclasses.replace(h, name=b"app".ljust(32, b"\0"))
    assert vux_tool.ImageHeader.parse(h.pack()[:63]) is None


def test_v4_image_is_header_then_payload():
    raw, h = vux_tool.build_vux9_v4_image(bytes(range(5)), name="a")
    assert raw[vux_tool.HEADER_LEN :] == bytes(range(5)), "no padding"
    assert (h.load_addr, h.entry_point, h.size) == (vux_tool.XMEM_BASE, vux_tool.XMEM_BASE, 5)
    raw, h = vux_tool.build_vux9_v4_image(bytes.fromhex("1234abcd5678"), mode="hack")
    assert raw[vux_tool.HEADER_LEN :] == bytes.fromhex("3412cdab78560000"), "pack_hack's word order"
    assert (h.load_addr, h.mode) == (0, vux_tool.MODE_HACK)


def test_constants_match_fw_common():
    with open(HEADER_RS) as f:
        rust = {m[1]: int(m[2].replace("_", ""), 0) for m in re.finditer(r"pub const (\w+): \w+ = (\w+);", f.read())}
    names = ["VUX_MAGIC", "HEADER_LEN", "SECTOR", "MODE_HACK", "MODE_RISCV", "HEADER_VERSION_V4"]
    for name in names:
        assert rust[name] == getattr(vux_tool, name), name
    # Field offsets: the order and sizes of HEADER_FORMAT
    offsets, at = {}, 0
    fields = ("magic", "header_version", "reserved", *V4_FIELDS)
    for field, code in zip(fields, re.findall(r"\d*[A-Za-z]", vux_tool.HEADER_FORMAT[1:]), strict=True):
        offsets[field] = at
        at += struct.calcsize("<" + code)
    for field, off in offsets.items():
        assert rust[f"OFF_{field.upper()}"] == off, field
    with open(MAP_RS) as f:
        m = re.search(r"pub const XMEM_BASE: usize = (\w+);", f.read())
    assert m and int(m[1].replace("_", ""), 0) == vux_tool.XMEM_BASE
