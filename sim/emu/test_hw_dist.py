# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side checks of the board-test script's handling of a `make dist` tree (no board).

`test_hardware.py --dist DIR` checks the first stage's version against the one DIR ships
(MANIFEST.json), not the working tree's main.rs: a dist of another commit has another
first stage in its bitstream, and installs its own second stage.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_hardware  # noqa: E402


def make_dist(tmp_path, s1_version):
    """The files test_hardware needs from a dist, with a MANIFEST naming s1_version."""
    for rel in test_hardware.DIST_FILES.values():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"\0" * 8)
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"stage1_version": s1_version}))
    return str(tmp_path)


def run_main(monkeypatch, argv):
    """test_hardware.main() with argv, the board replaced; the S1_VERSION and FILES it ran with."""
    seen: dict = {}

    def suite(port="auto", baud=115200):
        seen.update(s1=test_hardware.S1_VERSION, files=dict(test_hardware.FILES))
        return True

    monkeypatch.setattr(test_hardware, "S1_VERSION", test_hardware.S1_VERSION)
    monkeypatch.setattr(test_hardware, "FILES", dict(test_hardware.FILES))
    monkeypatch.setattr(test_hardware, "run_hardware_test_suite", suite)
    monkeypatch.setattr(sys, "argv", ["test_hardware.py", *argv])
    test_hardware.main()
    return seen


def test_dist_stage1_version_comes_from_the_manifest(tmp_path, monkeypatch):
    own = test_hardware.stage1_version()
    dist = make_dist(tmp_path, own + 7)
    assert test_hardware.dist_stage1_version(dist) == own + 7
    seen = run_main(monkeypatch, ["--dist", dist])
    assert seen["s1"] == own + 7
    assert seen["files"]["stage2"] == os.path.join(dist, "sd", "VUX9K", "STAGE2.BIN")


def test_without_dist_the_version_comes_from_main_rs(monkeypatch):
    seen = run_main(monkeypatch, [])
    assert seen["s1"] == test_hardware.stage1_version()


def test_installs_carry_the_test_versions():
    stage2 = test_hardware.vux_tool.build_vux9_v4_image(b"\x13\0\0\0" * 4, name="S2", load_addr=0x80700000)[0]
    data = test_hardware.with_version(stage2, 9001)
    h = test_hardware.vux_tool.ImageHeader.parse(data)
    assert h is not None and (h.version, h.load_addr, h.name.rstrip(b"\0")) == (9001, 0x80700000, b"S2")
    assert data[64:] == stage2[64:]
    out = "[S2] a0=0x00000001 a1=0x00000800 a2=0x00000001\n"
    assert test_hardware.handoff(out) == (1, 2048, 1)
    assert test_hardware.handoff("no handoff") is None
