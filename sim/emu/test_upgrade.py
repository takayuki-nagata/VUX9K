# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The upgrade from the previous release (v0.2.x, the Boot Manager's slots) on the emulator:
a card as v0.2.x left it, its slots in LBA 64-703 written by that release's own tool and
files, and a FAT32 partition after them, given to this tree's first stage; then
docs/APP_DEVELOPMENT.md's "Upgrading from v0.2.x" with this tree's tools/vux_tool.py, command
by command (`main()` on an EmuSerial, in emulated time). The slots stay as they were: neither
stage writes outside the partition. The previous release is the Makefile's PREV_RELEASE
(`make prev-release`); its SHA256SUMS are checked. The board half, the bitstream changed from
the old one to the new, is scripts/test_hw_upgrade.py."""

import importlib.util
import json
import os
import re
import sys

import pytest
from s1_env import BOOT_CYCLES, FAT_LBA, S1_PROMPT, S2_PROMPT, STAGE1_VERSION, app, card, stage2, start
from test_hello_demo import LAST_LINE, hello_regs
from vux9k import REPO_ROOT, EmuClock, EmuSerial, vux_tool, wait_for

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import release_check  # noqa: E402

with open(os.path.join(REPO_ROOT, "Makefile")) as _f:
    _m = re.search(r"^PREV_RELEASE = (\S+)$", _f.read(), re.M)
    assert _m, "PREV_RELEASE not found in the Makefile"
    PREV_RELEASE = _m.group(1)
PREV_DIR = os.path.join(REPO_ROOT, "build", "previous", f"vux9k-{PREV_RELEASE}")
HACK_DONE = b"ALL HACK C FIRMWARE TESTS PASSED (100%)!"
# The first stage's soft reset to the second stage's prompt (its banner, the key window,
# the 35 KB second stage from the card)
S2_BOOT_CYCLES = BOOT_CYCLES


@pytest.fixture(scope="module")
def prev():
    """The previous release's dist (checked) and its own vux_tool, as a separate module."""
    assert os.path.isdir(PREV_DIR), f"{PREV_DIR} missing; run `make prev-release`"
    release_check.check_sums(PREV_DIR)
    spec = importlib.util.spec_from_file_location("vux_tool_prev", os.path.join(PREV_DIR, "tools", "vux_tool.py"))
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def v02_card(prev) -> bytes:
    """A FAT32 card (partition at FAT_LBA, nothing on it yet) with the slots v0.2.x's docs had
    the user write: the Boot Manager in slot 0 with its version, the Zephyr demo in slot 1,
    the Hack demo in slot 2."""
    assert FAT_LBA >= prev.slot_sector(10), "v0.2.x needs the partition after its slots"
    img = bytearray(card())
    with open(os.path.join(PREV_DIR, "MANIFEST.json")) as f:
        bm_version = int(json.load(f)["boot_manager_version"])
    for slot, rel, mode, name, version in (
        (0, "boot-manager.bin", "riscv", "Boot Manager", bm_version),
        (1, "demos/zephyr-demo.bin", "riscv", "Zephyr demo", 1),
        (2, "demos/hack-demo.bin", "hack", "Hack demo", 1),
    ):
        raw, _ = prev.build_vux9_image(os.path.join(PREV_DIR, rel), slot=slot, name=name, mode=mode, version=version)
        at = prev.slot_sector(slot) * 512
        img[at : at + len(raw)] = raw
    return bytes(img)


def test_upgrade_from_the_previous_release(prev, monkeypatch, capsys, tmp_path):
    image = v02_card(prev)
    slots = image[512 : FAT_LBA * 512]
    soc = start(image)
    monkeypatch.setattr(vux_tool, "time", EmuClock(soc))
    monkeypatch.setattr(vux_tool, "open_port", lambda *a, **kw: EmuSerial(soc, timeout=0.05))

    def tool(*argv) -> str:
        monkeypatch.setattr(sys, "argv", ["vux_tool.py", "--port", "socket://emulator", *argv])
        vux_tool.main()
        return capsys.readouterr().out

    # The first stage finds no second stage on the old card and says why
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert "[BOOT] STAGE2.BIN: not found; STAGE2.OLD: not found\n" in out, out
    assert f"[FAT] FAT32 at LBA {FAT_LBA}" in tool("sd-info")

    # The documented steps, the files as the dist ships them
    stage2_bin = tmp_path / "STAGE2.BIN"
    stage2_bin.write_bytes(stage2())
    assert "[INSTALL] OK" in tool("install-stage2", str(stage2_bin))
    tool("mkdir", "VUX9K/APPS")
    for name in ("HELLO.BIN", "HACK.BIN"):
        (tmp_path / name).write_bytes(app(name))

    assert "[PUT] OK" in tool("put", str(tmp_path / "HELLO.BIN"), "VUX9K/APPS/HELLO.BIN")
    mark = len(soc.uart_received())
    assert "[RUN] " in tool("run-app", "VUX9K/APPS/HELLO.BIN")
    out = wait_for(soc, LAST_LINE, BOOT_CYCLES, start=mark)
    assert hello_regs(out) == (1, FAT_LBA, STAGE1_VERSION)
    soc.uart_send(b"r")  # back to the first stage, which boots the installed second stage
    wait_for(soc, S2_PROMPT, S2_BOOT_CYCLES, start=len(soc.uart_received()))

    # Beyond the doc's steps: a Hack application too, in its segments
    assert "[PUT] OK" in tool("put", str(tmp_path / "HACK.BIN"), "VUX9K/APPS/HACK.BIN")
    mark = len(soc.uart_received())
    assert "[RUN] " in tool("run-app", "VUX9K/APPS/HACK.BIN")
    out = wait_for(soc, HACK_DONE, BOOT_CYCLES, start=mark)
    assert "[FAIL]" not in out
    wait_for(soc, S2_PROMPT, S2_BOOT_CYCLES, start=len(soc.uart_received()))

    # An application as v0.2.x wrote it (a v3 slot image) is refused: they are rebuilt
    raw, _ = prev.build_vux9_image(os.path.join(PREV_DIR, "demos", "zephyr-demo.bin"), slot=1, mode="riscv")
    (tmp_path / "OLD.BIN").write_bytes(raw)
    tool("put", str(tmp_path / "OLD.BIN"), "VUX9K/APPS/OLD.BIN")
    with pytest.raises(SystemExit, match="not a VUX9 v4 file"):
        tool("run-app", "VUX9K/APPS/OLD.BIN")
    # The doc's mkdir again, on a card that has the directory: an error, not a traceback
    with pytest.raises(SystemExit, match=r"\[ERR\]"):
        tool("mkdir", "VUX9K/APPS")

    assert soc.sd_image()[512 : FAT_LBA * 512] == slots, "the slots and the gap were written"
