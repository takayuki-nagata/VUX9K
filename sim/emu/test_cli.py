# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""The vux9k-emu command line: batch runs, --until, the TCP UART and SD write-back."""

import os
import socket
import subprocess
import time

from vux9k import REPO_ROOT, sd_image

EMU = os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")


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


def test_tcp_uart_session():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([EMU, "--tcp", str(port), "--speed", "0"], cwd=REPO_ROOT, stderr=subprocess.PIPE)
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
            buf = b""
            while not buf.endswith(b"vux> "):
                buf += conn.recv(4096)
            conn.sendall(b"h\r")
            while b"Available Commands" not in buf.split(b"vux> ", 1)[1]:
                buf += conn.recv(4096)
    finally:
        proc.wait(timeout=30)
    assert b"host closed the UART" in proc.stderr.read()


def test_sd_write_back(tmp_path):
    img = tmp_path / "sd.img"
    img.write_bytes(sd_image({}))
    before = img.read_bytes()
    # The Boot Manager's 'd' command reads sector 0; nothing writes, so the image is unchanged
    r = run("--sd", str(img), "--sd-write-back", "--until", "vux> ", "--cycles", "5000000")
    assert r.returncode == 0, r.stderr
    assert img.read_bytes() == before
