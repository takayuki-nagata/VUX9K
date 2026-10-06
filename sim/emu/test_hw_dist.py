# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side checks of the board-test scripts' handling of a `make dist` tree (no board).

`test_hardware.py --dist DIR` flashes slot 0 relative to the Boot Manager it tests, so the
version must be the one DIR ships (MANIFEST.json), not the working tree's main.rs: a dist
of another release has another Boot Manager.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_hardware  # noqa: E402


def make_dist(tmp_path, bm_version):
    """The files test_hardware needs from a dist, with a MANIFEST naming bm_version."""
    for rel in test_hardware.DIST_FILES.values():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"\0" * 8)
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"boot_manager_version": bm_version}))
    return str(tmp_path)


def run_main(monkeypatch, argv):
    """test_hardware.main() with argv, the board replaced; the BM_VERSION and FILES it ran with."""
    seen: dict = {}

    def suite(port="auto", baud=115200):
        seen.update(bm=test_hardware.BM_VERSION, files=dict(test_hardware.FILES))
        return True

    monkeypatch.setattr(test_hardware, "BM_VERSION", test_hardware.BM_VERSION)
    monkeypatch.setattr(test_hardware, "FILES", dict(test_hardware.FILES))
    monkeypatch.setattr(test_hardware, "run_hardware_test_suite", suite)
    monkeypatch.setattr(sys, "argv", ["test_hardware.py", *argv])
    test_hardware.main()
    return seen


def test_dist_boot_manager_version_comes_from_the_manifest(tmp_path, monkeypatch):
    own = test_hardware.boot_manager_version()
    dist = make_dist(tmp_path, own + 7)
    assert test_hardware.dist_boot_manager_version(dist) == own + 7
    seen = run_main(monkeypatch, ["--dist", dist])
    assert seen["bm"] == own + 7
    assert seen["files"]["boot_manager"] == os.path.join(dist, "boot-manager.bin")


def test_without_dist_the_version_comes_from_main_rs(monkeypatch):
    seen = run_main(monkeypatch, [])
    assert seen["bm"] == test_hardware.boot_manager_version()
