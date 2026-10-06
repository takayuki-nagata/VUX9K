# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side checks of the board-test scripts' handling of a `make dist` tree (no board).

`test_hardware.py --dist DIR` flashes slot 0 relative to the Boot Manager it tests, so the
version must be the one DIR ships (MANIFEST.json), not the working tree's main.rs: a dist
of another release has another Boot Manager. `test_hw_upgrade.py` (previous release ->
candidate) judges each load by the startup output of that boot only, although the bridge may
still hand over the previous image's lines first.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_hardware  # noqa: E402
import test_hw_upgrade  # noqa: E402


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


BANNER_12 = "\n=====\n  VUX9K Dual-ISA RISC-V / Hack SoC Boot Manager (v12)\n=====\n\nAvailable Commands:\nvux> "
BANNER_13 = BANNER_12.replace("(v12)", "(v13)")
UPDATE_13 = (
    "\n[UPDATE] Verified valid Boot Manager update (v13, CRC32: 0x1234ABCD). Auto-updating Lower I-RAM"
    " via Resident Loader...\n\n\n[UPDATE] Booted newly updated Boot Manager!\n\n"
)
# what the bridge may still hand over from the image before the load
STALE_UPDATE = UPDATE_13 + BANNER_13 + "1\n[RL] Slot 1\n[Rust App] done\n"


@pytest.mark.parametrize(
    "text, version, update_to, ok",
    [
        (BANNER_12, 12, None, True),
        (BANNER_12, 13, None, False),
        (UPDATE_13 + BANNER_13, 13, None, False),  # an update where none may happen
        (UPDATE_13 + BANNER_13, 13, 13, True),
        (BANNER_13, 13, 13, False),  # the newer one already in BRAM: nothing installed
        (UPDATE_13.replace("v13,", "v14,") + BANNER_13, 13, 13, False),
        (STALE_UPDATE + BANNER_12, 12, None, True),  # the previous boot's update doesn't count
        (STALE_UPDATE + UPDATE_13 + BANNER_13, 13, 13, True),
        (STALE_UPDATE + BANNER_12[:-5], 12, None, False),  # no prompt yet
    ],
)
def test_upgrade_startup_check(text, version, update_to, ok):
    seg = test_hw_upgrade.startup_segment(text)
    assert test_hw_upgrade.check_startup(seg, version, update_to)[0] is ok


def test_upgrade_loads_each_tool_as_its_own_module():
    path = os.path.join(REPO_ROOT, "tools", "vux_tool.py")
    a = test_hw_upgrade.load_tool(path, "vux_tool_test_a")
    b = test_hw_upgrade.load_tool(path, "vux_tool_test_b")
    try:
        assert a is not b and a.flash_slot is not b.flash_slot
        assert a.build_vux9_image(b"\x13\0\0\0", slot=1)[0] == b.build_vux9_image(b"\x13\0\0\0", slot=1)[0]
    finally:
        del sys.modules["vux_tool_test_a"], sys.modules["vux_tool_test_b"]
