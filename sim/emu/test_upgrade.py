# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The upgrade from the previous release (v0.3.x: the two-stage boot on a CPU without RV32M,
its timer at the TIMER page) on the emulator: a card as v0.3.x left it, its \\VUX9K\\ from
that release's dist, given to this tree's first stage on a CPU with M (the new bitstream,
written first), which refuses the old second stage (older than MIN_STAGE2_VERSION: it reads
mtime where the CLINT replaced the page) and stays at its prompt; then
docs/APP_DEVELOPMENT.md's "Upgrading from v0.3.x" with this tree's tools/vux_tool.py, command
by command (`main()` on an EmuSerial, in emulated time). And the wrong order, the card first:
the previous release's first stage on a CPU without M boots the new second stage, which says
so and stops. The previous release is the Makefile's PREV_RELEASE (`make prev-release`); its
SHA256SUMS are checked. The board half, the bitstream changed from the old one to the new, is
scripts/test_hw_upgrade.py."""

import json
import os
import re
import sys

import pytest
from s1_env import (
    BOOT_CYCLES,
    FAT_LBA,
    MIN_STAGE2_VERSION,
    S1_PROMPT,
    S2_BANNER,
    S2_PROMPT,
    STAGE1_VERSION,
    app,
    card,
    stage2,
    start,
)
from test_hello_demo import LAST_LINE, hello_regs
from vux9k import REPO_ROOT, EmuClock, EmuSerial, new_soc, preload_firmware, vux_tool, wait_for

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import release_check  # noqa: E402

with open(os.path.join(REPO_ROOT, "Makefile")) as _f:
    _m = re.search(r"^PREV_RELEASE = (\S+)$", _f.read(), re.M)
    assert _m, "PREV_RELEASE not found in the Makefile"
    PREV_RELEASE = _m.group(1)
PREV_DIR = os.path.join(REPO_ROOT, "build", "previous", f"vux9k-{PREV_RELEASE}")
HACK_DONE = b"ALL HACK C FIRMWARE TESTS PASSED (100%)!"
# What the second stage prints on a CPU without M (firmware/stage2/bootstrap/start.s)
NO_M = "VUX9K Stage 2 needs a CPU with RV32M: program the v0.4.0 bitstream or later. Stopped.\n"
# The first stage's soft reset to the second stage's prompt (its banner, the key window,
# the 35 KB second stage from the card)
S2_BOOT_CYCLES = BOOT_CYCLES


@pytest.fixture(scope="module")
def prev():
    """The previous release's dist (checked) and its manifest."""
    assert os.path.isdir(PREV_DIR), f"{PREV_DIR} missing; run `make prev-release`"
    release_check.check_sums(PREV_DIR)
    with open(os.path.join(PREV_DIR, "MANIFEST.json")) as f:
        return json.load(f)


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def prev_file(rel: str) -> bytes:
    return read(os.path.join(PREV_DIR, "sd", "VUX9K", rel))


def v03_card() -> bytes:
    """A FAT32 card as v0.3.x's docs had the user prepare it: its second stage as
    \\VUX9K\\STAGE2.BIN and its demos in \\VUX9K\\APPS\\ (the small ones)."""
    names = ("STAGE2.BIN", "APPS/HELLO.BIN", "APPS/HACK.BIN")
    return card({f"VUX9K/{rel}": prev_file(rel) for rel in names})


def test_upgrade_from_the_previous_release(prev, monkeypatch, capsys, tmp_path):
    soc = start(v03_card())
    monkeypatch.setattr(vux_tool, "time", EmuClock(soc))
    monkeypatch.setattr(vux_tool, "open_port", lambda *a, **kw: EmuSerial(soc, timeout=0.05))

    def tool(*argv) -> str:
        monkeypatch.setattr(sys, "argv", ["vux_tool.py", "--port", "socket://emulator", *argv])
        vux_tool.main()
        return capsys.readouterr().out

    # The new bitstream first: its first stage doesn't start the old second stage (its timer
    # is gone), says why and stays at its prompt
    old = prev["stage2_version"]
    assert old < MIN_STAGE2_VERSION
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    want = f"[BOOT] STAGE2.BIN: too old, needs v{MIN_STAGE2_VERSION} (install-stage2): v{old}; STAGE2.OLD: not found\n"
    assert want in out, out
    assert "VUX9K Stage 2" not in out, out

    # The documented steps, the files as the dist ships them
    stage2_bin = tmp_path / "STAGE2.BIN"
    stage2_bin.write_bytes(stage2())
    out = tool("install-stage2", str(stage2_bin))
    assert "[INSTALL] OK" in out and S2_BANNER in out, out
    for name in ("HELLO.BIN", "HACK.BIN"):
        (tmp_path / name).write_bytes(app(name))
        assert "[PUT] OK" in tool("put", str(tmp_path / name), f"VUX9K/APPS/{name}")
    assert "STAGE2.OLD" in tool("ls", "VUX9K")
    mark = len(soc.uart_received())
    assert "[RUN] " in tool("run-app", "VUX9K/APPS/HELLO.BIN")
    out = wait_for(soc, LAST_LINE, BOOT_CYCLES, start=mark)
    assert hello_regs(out) == (1, FAT_LBA, STAGE1_VERSION)
    soc.uart_send(b"r")  # back to the first stage, which boots the installed second stage
    out = wait_for(soc, S2_PROMPT, S2_BOOT_CYCLES, start=len(soc.uart_received()))
    assert S2_BANNER in out, out

    # Beyond the doc's steps: a Hack application too, in its segments
    mark = len(soc.uart_received())
    assert "[RUN] " in tool("run-app", "VUX9K/APPS/HACK.BIN")
    out = wait_for(soc, HACK_DONE, BOOT_CYCLES, start=mark)
    assert "[FAIL]" not in out
    wait_for(soc, S2_PROMPT, S2_BOOT_CYCLES, start=len(soc.uart_received()))


def test_the_card_before_the_bitstream_stops_the_second_stage(prev):
    """The new second stage on the card while the flash still holds the previous release's
    bitstream (its first stage, a CPU without M): the second stage says what to do and stops
    before its first M instruction. The card is left as it is: programming the bitstream then
    is the whole fix."""
    soc = new_soc()
    soc.m_ext = False
    preload_firmware(soc, os.path.join(PREV_DIR, "emu", "firmware"))
    image = card({"VUX9K/STAGE2.BIN": stage2(), "VUX9K/APPS/HELLO.BIN": app("HELLO.BIN")})
    soc.sd_insert(image)
    out = wait_for(soc, b"Stopped.\n", BOOT_CYCLES)
    assert f"VUX9K Stage 1 (v{prev['stage1_version']})" in out, out
    assert out.endswith(NO_M), out
    assert soc.sd_image() == image
