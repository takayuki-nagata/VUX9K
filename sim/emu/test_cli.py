# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The vux9k-emu command line: batch runs, --until, the TCP UART and SD write-back."""

import os
import socket
import subprocess
import sys
import time

import pytest
from s1_env import STAGE2_VERSION
from vux9k import REPO_ROOT, sd_image

# VUX9K_EMU_BIN: an instrumented build (`make coverage-rust`)
EMU = os.environ.get("VUX9K_EMU_BIN") or os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")


def run(*args, timeout=60):
    return subprocess.run([EMU, *args], cwd=REPO_ROOT, capture_output=True, timeout=timeout)


def test_batch_until_prompt():
    # The default card has no FAT32 volume: the first stage stops at its prompt
    r = run("--until", "s1> ", "--cycles", "10000000")
    assert r.returncode == 0, r.stderr
    assert b"VUX9K Stage 1" in r.stdout
    assert r.stdout.endswith(b"s1> ")
    assert b"--until text received" in r.stderr


def test_until_not_reached_fails():
    r = run("--until", "never printed", "--cycles", "100000")
    assert r.returncode == 1
    assert b"cycle budget" in r.stderr


def test_hex_runs_as_rv32():
    # --hex fills the ROM, which starts RV32 (the ISA isn't guessed): the Hack demo's words garble
    r = run(
        "--no-firmware", "--no-card", "--hex", "build/hack/firmware.hex", "--until", "(100%)!", "--cycles", "2000000"
    )
    assert r.returncode == 1
    assert b"ALL HACK C FIRMWARE TESTS PASSED" not in r.stdout


def test_hack_bin_with_mode():
    # The image vux_tool.py mkv4 --mode hack wraps, as an application developer runs it: in
    # Hack's instruction segment in the PSRAM, started in Hack at 0
    r = run("--no-firmware", "--no-card", "--load", "build/hack/firmware.bin", "--mode", "hack", "--until", "(100%)!")
    assert r.returncode == 0, r.stderr
    assert b"ALL HACK C FIRMWARE TESTS PASSED" in r.stdout


def test_load_without_mode_is_raw_words():
    # Without --mode the file is little-endian words in the ROM, run as RV32: a Hack .bin garbles
    r = run(
        "--no-firmware", "--no-card", "--load", "build/hack/firmware.bin", "--until", "(100%)!", "--cycles", "2000000"
    )
    assert r.returncode == 1
    assert b"ALL HACK C FIRMWARE TESTS PASSED" not in r.stdout


def where_am_i() -> bytes:
    """An RV32 image that prints P when it runs from the PSRAM, I from the ROM."""
    from rv32_asm import Asm  # on sys.path once vux9k is imported

    a = Asm()
    a.li("s0", 0x4000_0000)  # UART data
    a.auipc("t0", 0)
    a.lui("t1", 0x80000)
    a.li("a1", ord("I"))
    a.bltu("t0", "t1", "out")
    a.li("a1", ord("P"))
    a.label("out")
    a.sb("a1", 0, "s0")
    a.label("halt")
    a.j("halt")
    return b"".join(w.to_bytes(4, "little") for w in a.assemble())


@pytest.mark.parametrize(
    "args,where",
    [([], b"P"), (["--load-addr", "0x8000_0100"], b"P"), (["--load-addr", "0"], b"I")],
    ids=["default", "psram", "rom"],
)
def test_mode_riscv_loads_at_the_load_address(tmp_path, args, where):
    # RV32 applications start where the second stage's `run` starts them: the PSRAM
    image = tmp_path / "app.bin"
    image.write_bytes(where_am_i())
    app = ("--no-firmware", "--no-card", "--load", str(image), "--mode", "riscv")
    r = run(*app, *args, "--until", "P", "--cycles", "10000")
    assert r.stdout == where, (r.stdout, r.stderr)


def test_mode_rejects_an_image_too_big_for_the_board(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x13\x00\x00\x00" * (14 * 1024 // 4 + 1))
    r = run("--no-firmware", "--load", str(big), "--mode", "riscv", "--load-addr", "0")
    assert r.returncode != 0
    assert b"fits on the board" in r.stderr
    # The PSRAM takes it
    r = run("--no-firmware", "--load", str(big), "--mode", "riscv", "--cycles", "1000")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "args,error",
    [
        (["--load-addr", "0x80000000"], b"--load-addr needs --mode"),
        (
            ["--load", "build/hack/firmware.bin", "--mode", "hack", "--load-addr", "0x80000000"],
            b"Hack instruction segment",
        ),
        (["--load", "build/hack/firmware.bin", "--mode", "riscv", "--load-addr", "0x8000"], b"into the PSRAM"),
        (["--load", "build/hack/firmware.bin", "--mode", "riscv", "--load-addr", "nowhere"], b"--load-addr"),
    ],
    ids=["no-mode", "hack", "outside", "not-a-number"],
)
def test_load_addr_errors(args, error):
    r = run("--no-firmware", *args)
    assert r.returncode != 0
    assert error in r.stderr, r.stderr


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tcp_session(extra_args, dialog):
    """Run the emulator with its UART on TCP; dialog(recv_until, send) drives it."""
    port = free_port()
    proc = subprocess.Popen(
        [EMU, "--tcp", str(port), "--speed", "0", *extra_args], cwd=REPO_ROOT, stderr=subprocess.PIPE
    )
    try:
        for _ in range(100):
            try:
                conn = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("emulator did not listen")
        with conn:
            buf = bytearray()

            def recv_until(token):
                while token not in buf:
                    chunk = conn.recv(4096)
                    assert chunk, f"connection closed waiting for {token!r}: {bytes(buf)!r}"
                    buf.extend(chunk)
                out = bytes(buf[: buf.index(token) + len(token)])
                del buf[: len(out)]
                return out

            dialog(recv_until, conn.sendall)
    finally:
        proc.wait(timeout=30)
    assert proc.stderr
    return proc.stderr.read()


def test_tcp_uart_session():
    def dialog(recv_until, send):
        recv_until(b"s1> ")
        send(b"h\r")
        recv_until(b"Commands:")

    assert b"host closed the UART" in tcp_session([], dialog)


def test_mkfat_card_boots_the_second_stage(tmp_path):
    img = tmp_path / "sd.img"
    tool = [sys.executable, os.path.join(REPO_ROOT, "tools", "vux_tool.py")]
    stage2 = os.path.join(REPO_ROOT, "build", "firmware", "stage2", "STAGE2.BIN")
    subprocess.run([*tool, "mkfat", str(img), "--file", f"VUX9K/STAGE2.BIN={stage2}"], check=True)

    def dialog(recv_until, send):
        out = recv_until(b"s2> ")
        assert f"[BOOT] STAGE2.BIN v{STAGE2_VERSION} ".encode() in out and b"[S2] a0=0x00000001 a1=0x00000800" in out, (
            out
        )

    tcp_session(["--sd", str(img)], dialog)


def test_sd_write_back(tmp_path):
    img = tmp_path / "sd.img"
    img.write_bytes(sd_image({}))
    before = img.read_bytes()
    # The first stage reads sector 0; nothing writes, so the image is unchanged
    r = run("--sd", str(img), "--sd-write-back", "--until", "s1> ", "--cycles", "10000000")
    assert r.returncode == 0, r.stderr
    assert img.read_bytes() == before
