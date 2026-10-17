# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side checks of the board-test script (scripts/test_hardware.py), no board.

`test_hardware.py --dist DIR` checks the first stage's version against the one DIR ships
(MANIFEST.json), not the working tree's main.rs: a dist of another commit has another
first stage in its bitstream, and installs its own second stage and demos. The demos'
expectations are the emulator tests'. With VUX9K_HW_SUITE_EMU=1 the whole suite also runs
against `vux9k-emu --tcp` (~3 min at the board's speed; the tests that need a bitstream
load are skipped there).
"""

import json
import os
import socket
import subprocess
import sys
import threading

import pytest

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
    (tmp_path / test_hardware.DIST_APPS_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"stage1_version": s1_version}))
    return str(tmp_path)


def run_main(monkeypatch, argv):
    """test_hardware.main() with argv, the board replaced; the S1_VERSION and FILES it ran with."""
    seen: dict = {}

    def suite(port="auto", baud=115200, loader=None, reload=True):
        seen.update(s1=test_hardware.S1_VERSION, files=dict(test_hardware.FILES), reload=reload)
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
    assert seen["files"]["apps"] == os.path.join(dist, "sd", "VUX9K", "APPS")
    assert seen["files"]["pack_fs"] == os.path.join(dist, "bitstream", "pack.fs")
    assert seen["reload"]
    assert not run_main(monkeypatch, ["--dist", dist, "--no-reload"])["reload"]


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


def test_ls_and_hello_parsing():
    out = "SUB          <DIR>\nDATA.TXT          792\nLONGNA~1.TXT        3  Long name.txt\n"
    assert test_hardware.ls_entries(out) == {"SUB": None, "DATA.TXT": 792, "LONGNA~1.TXT": 3}
    assert test_hardware.ls_entries("") == {}
    assert test_hardware.hello_regs("[HELLO] a0=0x00000000 a1=0x00002000 a2=0x00000002\n") == (0, 0x2000, 2)
    assert test_hardware.hello_regs("[S2] a0=0x00000000 a1=0x00002000 a2=0x00000002") is None


def test_demo_expectations_are_the_emulator_tests():
    import test_hack_demo
    import test_hello_demo

    assert test_hardware.HELLO_LAST.encode() == test_hello_demo.LAST_LINE
    assert test_hardware.HACK_DONE.encode() == test_hack_demo.DONE
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_hack_demo.py")).read()
    assert all(f'"{ln}"' in src for ln in test_hardware.HACK_LINES)
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_zephyr_demo.py")).read()
    assert all(ln in src for ln in (*test_hardware.ZEPHYR_LINES, test_hardware.ZEPHYR_END))
    import test_zephyr_bc

    assert test_hardware.BC_LINES == tuple(test_zephyr_bc.SELF_TESTS)
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_zephyr_bc.py")).read()
    assert all(f'"{line}"' in src and (not want or f'"{want}"' in src) for line, want in test_hardware.BC_REPL)


def test_irq_echo_files_come_from_the_dist(tmp_path):
    import hw_irq_echo

    dist = make_dist(tmp_path, 1)
    app, pack_fs = hw_irq_echo.resolve_files(hw_irq_echo.parse_args(["--dist", dist]))
    assert app == os.path.join(dist, "sd", "VUX9K", "APPS", "IRQECHO.BIN")
    assert pack_fs == os.path.join(dist, "bitstream", "pack.fs")  # not the working tree's
    app, pack_fs = hw_irq_echo.resolve_files(hw_irq_echo.parse_args(["--bin", "X.BIN"]))
    assert (app, pack_fs) == ("X.BIN", os.path.join(REPO_ROOT, "build", "synth", "pack.fs"))
    with pytest.raises(SystemExit):
        hw_irq_echo.resolve_files(hw_irq_echo.parse_args([]))
    # A raw zephyr.bin is wrapped to load where the board vux9k links it, the PSRAM's start
    h = test_hardware.vux_tool.ImageHeader.parse(hw_irq_echo.v4_file(b"\x13\0\0\0"))
    assert h is not None and (h.mode, h.load_addr) == (test_hardware.vux_tool.MODE_RISCV, 0x8000_0000)


def test_bitstream_load_errors(tmp_path):
    assert test_hardware.load_bitstream(None, str(tmp_path / "pack.fs")) == "no openFPGALoader (--loader)"
    assert "missing" in test_hardware.load_bitstream("openFPGALoader", str(tmp_path / "pack.fs"))


EMU = os.environ.get("VUX9K_EMU_BIN") or os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")


@pytest.mark.skipif(
    os.environ.get("VUX9K_HW_SUITE_EMU") != "1", reason="make sim-hw-suite-emu runs it (~3 min, test-slow)"
)
def test_suite_on_the_emulator(tmp_path):
    img = tmp_path / "sd.img"
    img.write_bytes(test_hardware.vux_tool.build_fat32_image({}))  # no \VUX9K\: install makes it
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    emu = subprocess.Popen(
        [EMU, "--tcp", str(port), "--sd", str(img)], cwd=REPO_ROOT, stderr=subprocess.PIPE, text=True
    )
    # The emulator's stderr is read to its end in a thread (a full pipe would stop it);
    # the test waits for the listening line at most 30 s
    listening = threading.Event()
    log: list = []

    def drain():
        assert emu.stderr
        for line in emu.stderr:
            log.append(line)
            if "waiting for a UART client" in line:
                listening.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        assert listening.wait(30), f"the emulator did not listen: {''.join(log)[-2000:]}"
        r = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "scripts", "test_hardware.py"),
                "--port",
                f"socket://127.0.0.1:{port}",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        emu.kill()
        emu.wait()
    assert r.returncode == 0, r.stdout[-3000:]
    assert "Score       : 13/13" in r.stdout and "(and 2 skipped)" in r.stdout, r.stdout[-2000:]
