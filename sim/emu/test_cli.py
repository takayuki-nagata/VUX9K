# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The vux9k-emu command line: batch runs, --until, the TCP UART and SD write-back."""

import os
import socket
import struct
import subprocess
import sys
import time

from vux9k import REPO_ROOT, sd_image

# VUX9K_EMU_BIN: an instrumented build (`make coverage-rust`)
EMU = os.environ.get("VUX9K_EMU_BIN") or os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")


def run(*args, timeout=60):
    return subprocess.run([EMU, *args], cwd=REPO_ROOT, capture_output=True, timeout=timeout)


def test_batch_until_prompt():
    r = run("--until", "vux> ", "--cycles", "5000000")
    assert r.returncode == 0, r.stderr
    assert b"Boot Manager" in r.stdout
    assert r.stdout.endswith(b"vux> ")
    assert b"--until text received" in r.stderr


def test_until_not_reached_fails():
    r = run("--until", "never printed", "--cycles", "100000")
    assert r.returncode == 1
    assert b"cycle budget" in r.stderr


def test_hack_demo_without_firmware():
    r = run("--no-firmware", "--no-card", "--hex", "build/hack/firmware.hex", "--until", "(100%)!")
    assert r.returncode == 0, r.stderr
    assert b"ALL HACK C FIRMWARE TESTS PASSED" in r.stdout


def test_hack_bin_with_mode():
    # The image vux_tool.py flash-sd --mode hack writes, as an application developer runs it
    r = run("--no-firmware", "--no-card", "--load", "build/hack/firmware.bin", "--mode", "hack", "--until", "(100%)!")
    assert r.returncode == 0, r.stderr
    assert b"ALL HACK C FIRMWARE TESTS PASSED" in r.stdout


def test_load_without_mode_is_raw_words():
    # Without --mode the file is little-endian words and the ISA a guess: a Hack .bin garbles
    r = run(
        "--no-firmware", "--no-card", "--load", "build/hack/firmware.bin", "--until", "(100%)!", "--cycles", "2000000"
    )
    assert r.returncode == 1
    assert b"ALL HACK C FIRMWARE TESTS PASSED" not in r.stdout


def test_mode_rejects_an_image_too_big_for_the_board(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x13\x00\x00\x00" * (14 * 1024 // 4 + 1))
    r = run("--no-firmware", "--load", str(big), "--mode", "riscv")
    assert r.returncode != 0
    assert b"fits on the board" in r.stderr


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
        recv_until(b"vux> ")
        send(b"h\r")
        recv_until(b"Available Commands")

    assert b"host closed the UART" in tcp_session([], dialog)


def test_mkimg_slot_boots_from_the_boot_manager(tmp_path):
    payload = tmp_path / "hash.bin"
    # lui a1, 0x40000; addi a0, zero, '#'; sb a0, 0(a1); j .
    payload.write_bytes(struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F))
    img = tmp_path / "sd.img"
    tool = [sys.executable, os.path.join(REPO_ROOT, "tools", "vux_tool.py")]
    subprocess.run([*tool, "mkimg", str(img), "--slot", f"1:{payload}:riscv:Hash"], check=True)
    assert len(img.read_bytes()) == (128 + 1) * 512

    def dialog(recv_until, send):
        recv_until(b"vux> ")
        send(b"1")
        recv_until(b"[RL] Slot 1")
        recv_until(b"#")

    tcp_session(["--sd", str(img)], dialog)


def test_sd_write_back(tmp_path):
    img = tmp_path / "sd.img"
    img.write_bytes(sd_image({}))
    before = img.read_bytes()
    # The Boot Manager's 'd' command reads sector 0; nothing writes, so the image is unchanged
    r = run("--sd", str(img), "--sd-write-back", "--until", "vux> ", "--cycles", "5000000")
    assert r.returncode == 0, r.stderr
    assert img.read_bytes() == before
