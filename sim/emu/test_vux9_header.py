# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The VUX9 header on both sides: tools/vux_tool.py (writes slots) and fw_common (reads them).

firmware/fw_common/tests/vux9_vectors/ holds headers vux_tool made, each with the fields
fw_common's SlotHeader::parse must return (`<case>.txt`, `field value` lines, or `invalid`).
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

VECTOR_DIR = os.path.join(REPO_ROOT, "firmware", "fw_common", "tests", "vux9_vectors")
HEADER_RS = os.path.join(REPO_ROOT, "firmware", "fw_common", "src", "header.rs")
UPDATE = os.environ.get("VUX9_VECTORS_UPDATE") == "1"

NOPS = bytes.fromhex("13000000") * 4
FIELDS = ("header_version", "flags", "mode", "size", "load_addr", "entry_point", "crc32", "version", "name")


def _image(**kw):
    raw, _ = vux_tool.build_vux9_image(kw.pop("payload", NOPS), **kw)
    return bytearray(raw[: vux_tool.HEADER_LEN])


def _patched(base, offset, fmt, value):
    struct.pack_into(fmt, base, offset, value)
    return base


# case -> 64-byte header. The invalid ones are changed after packing, since vux_tool
# writes only valid headers (except for the magic, which tests override).
CASES = {
    "bootmgr_v11": lambda: _image(slot=0, name="BootMgr", mode="riscv", version=11),
    "hack_v1": lambda: _image(slot=2, name="Hk", mode="hack", version=1),
    "riscv_loaded": lambda: _image(
        payload=bytes(range(256)) * 3,
        slot=9,
        name="N" * 32,
        mode="riscv",
        version=0xFFFFFFFF,
        load_addr=0x1000,
        entry_point=0x1004,
    ),
    "bad_magic": lambda: _image(slot=1, name="x", mode="riscv", magic_override=0x56555838),
    "bad_header_version": lambda: _patched(_image(slot=1, name="x", mode="riscv"), 4, "<H", 2),
    "not_valid": lambda: _patched(_image(slot=1, name="x", mode="riscv"), 6, "<H", 0),
}


def _expected(header):
    h = vux_tool.SlotHeader.parse(bytes(header))
    if h is None:
        return "invalid\n"
    values = {f: getattr(h, f) for f in FIELDS}
    values["name"] = h.name.hex()
    return "".join(f"{f} {values[f]}\n" for f in FIELDS)


@pytest.mark.parametrize("case", sorted(CASES))
def test_vectors_match_vux_tool(case):
    header = bytes(CASES[case]())
    expected = _expected(header)
    bin_path = os.path.join(VECTOR_DIR, f"{case}.bin")
    txt_path = os.path.join(VECTOR_DIR, f"{case}.txt")
    if UPDATE:
        os.makedirs(VECTOR_DIR, exist_ok=True)
        with open(bin_path, "wb") as f:
            f.write(header)
        with open(txt_path, "w") as f:
            f.write(expected)
    with open(bin_path, "rb") as f:
        assert f.read() == header, f"{case}.bin differs from vux_tool's (VUX9_VECTORS_UPDATE=1 regenerates)"
    with open(txt_path) as f:
        assert f.read() == expected, f"{case}.txt differs from vux_tool's parse"


def test_no_stale_vectors():
    files = {os.path.splitext(n)[0] for n in os.listdir(VECTOR_DIR)}
    assert files == set(CASES), "vectors without a case (or the reverse); remove or regenerate"


def test_pack_parse_round_trip():
    h = vux_tool.SlotHeader(mode=vux_tool.MODE_RISCV, size=5, crc32=7, name=b"app", version=3, load_addr=8)
    assert vux_tool.SlotHeader.parse(h.pack()) == dataclasses.replace(h, name=b"app".ljust(32, b"\0"))
    assert int.from_bytes(h.pack()[:4], "little") == vux_tool.VUX_MAGIC


def test_constants_match_fw_common():
    with open(HEADER_RS) as f:
        rust = {m[1]: int(m[2].replace("_", ""), 0) for m in re.finditer(r"pub const (\w+): \w+ = (\w+);", f.read())}
    names = ["VUX_MAGIC", "HEADER_VERSION", "HEADER_LEN", "SECTOR", "FLAG_VALID", "FLAG_SYSTEM", "MODE_HACK"]
    names += ["MODE_RISCV"]
    for name in names:
        assert rust[name] == getattr(vux_tool, name), name
    # Field offsets: the order and sizes of HEADER_FORMAT
    offsets, at = {}, 0
    for field, code in zip(("magic", *FIELDS), re.findall(r"\d*[A-Za-z]", vux_tool.HEADER_FORMAT[1:]), strict=True):
        offsets[field] = at
        at += struct.calcsize("<" + code)
    for field, off in offsets.items():
        assert rust[f"OFF_{field.upper()}"] == off, field
    assert vux_tool.slot_sector(1) == 128
