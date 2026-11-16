# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""scripts/make_linux_dist.py (make linux-dist) on fake make linux and buildroot outputs: the
card's files (Image, the device tree, the programs buildroot's packages put in images/sd/ and
the archive of MicroPython's modules made from images/pylib/, its version beside it) with
their sizes and sha256 in MANIFEST.json and in SHA256SUMS, the build's sizes; the archive's
members, metadata and version; refused when images/sd/ holds a file outside VUX9K/LINUX/BIN/ or a script with CR LF
lines, images/pylib/ anything but .mpy modules, or an input is missing. Needs no Linux build."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile

from vux9k import REPO_ROOT

SCRIPT = os.path.join(REPO_ROOT, "scripts", "make_linux_dist.py")
# A sample script for the card (package vux9k-scripts)
SCRIPT_FILE = b"#!/bin/sh\nexec awk 'END { print 1 }' /proc/meminfo\n"
# Two compiled modules (package vux9k-micropython's images/pylib/), long names
MODULES = {"bc_parser.mpy": b"M\x06\x00\x1fparser", "bc_selftest.mpy": b"M\x06\x00\x1fselftest"}
PYLIB_TAR = "VUX9K/LINUX/PYLIB.TAR"
PYLIB_VER = "VUX9K/LINUX/PYLIB.VER"


def write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def fake_build(tmp_path):
    linux, buildroot = tmp_path / "linux", tmp_path / "buildroot"
    write(linux / "arch" / "riscv" / "boot" / "Image", b"I" * 10)
    write(linux / "vux9k.dtb", b"D" * 3)
    write(linux / ".config", b"CONFIG_X=y\n")
    write(buildroot / "images" / "rootfs.cpio", b"R" * 7)
    write(buildroot / "target" / "bin" / "busybox", b"B" * 5)
    write(buildroot / "images" / "sd" / "VUX9K" / "LINUX" / "BIN" / "memprobe", b"M" * 4)
    write(buildroot / "images" / "sd" / "VUX9K" / "LINUX" / "BIN" / "bbsed", b"S" * 6)
    write(buildroot / "images" / "sd" / "VUX9K" / "LINUX" / "BIN" / "meminfo", SCRIPT_FILE)
    write(buildroot / "images" / "sd" / "VUX9K" / "LINUX" / "BIN" / "MPY", b"P" * 8)
    for name, data in MODULES.items():
        write(buildroot / "images" / "pylib" / name, data)
    return linux, buildroot


def run(linux, buildroot, out):
    argv = [sys.executable, SCRIPT, "--linux-out", str(linux), "--buildroot-out", str(buildroot)]
    argv += ["--out", str(out), "--linux-version", "7.3", "--buildroot-version", "test"]
    return subprocess.run(argv, capture_output=True, text=True)


def test_tree_with_the_cards_programs(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    out = tmp_path / "out"
    r = run(linux, buildroot, out)
    assert r.returncode == 0, r.stderr
    manifest = json.loads((out / "MANIFEST.json").read_text())
    # The card's busyboxes' sizes too (the memory table), MicroPython's and its archive's, not
    # memprobe's or the script's
    tar_size = (out / PYLIB_TAR).stat().st_size
    assert manifest["sizes"] == {
        "image": 10,
        "rootfs_cpio": 7,
        "busybox": 5,
        "mpy": 8,
        "bbsed": 6,
        "pylib_tar": tar_size,
    }
    card = manifest["card_files"]
    assert sorted(card) == [
        "VUX9K/LINUX/BIN/MPY",
        "VUX9K/LINUX/BIN/bbsed",
        "VUX9K/LINUX/BIN/meminfo",
        "VUX9K/LINUX/BIN/memprobe",
        "VUX9K/LINUX/Image",
        PYLIB_TAR,
        PYLIB_VER,
        "VUX9K/LINUX/vux9k.dtb",
    ]
    assert card["VUX9K/LINUX/BIN/memprobe"] == {"size": 4, "sha256": hashlib.sha256(b"M" * 4).hexdigest()}
    assert (out / "VUX9K" / "LINUX" / "BIN" / "memprobe").read_bytes() == b"M" * 4
    # The script as it is, executable
    meminfo = out / "VUX9K" / "LINUX" / "BIN" / "meminfo"
    assert meminfo.read_bytes() == SCRIPT_FILE and os.stat(meminfo).st_mode & 0o111 == 0o111
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert sorted(sums) == sorted([*card, "MANIFEST.json", "linux.config"])
    for rel, digest in sums.items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest, rel


def test_refuses_a_card_file_outside_bin(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    write(buildroot / "images" / "sd" / "VUX9K" / "STAGE2.BIN", b"S")
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "is not under VUX9K/LINUX/BIN/" in r.stderr, r.stderr


def test_refuses_a_script_with_crlf_lines(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    write(buildroot / "images" / "sd" / "VUX9K" / "LINUX" / "BIN" / "meminfo", SCRIPT_FILE.replace(b"\n", b"\r\n"))
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "meminfo is a script with CR LF lines" in r.stderr, r.stderr


def test_refuses_a_missing_input(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    (buildroot / "target" / "bin" / "busybox").unlink()
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "busybox" in r.stderr, r.stderr


def pylib_members(path) -> list[tarfile.TarInfo]:
    with tarfile.open(path) as tar:
        return tar.getmembers()


def test_pylib_archive(tmp_path):
    """VUX9K/LINUX/PYLIB.TAR: an uncompressed ustar archive of PYLIB/ with the modules under
    their long names, PYLIB/VERSION last (MANIFEST.json's pylib_version, and VUX9K/LINUX/PYLIB.VER
    the same bytes), owner 0, no user or group names, one fixed time; the same bytes from a
    second run."""
    linux, buildroot = fake_build(tmp_path)
    out = tmp_path / "out"
    assert run(linux, buildroot, out).returncode == 0
    data = (out / PYLIB_TAR).read_bytes()
    # ustar's magic in the first header, and no compression
    assert data[257:263] == b"ustar\x00" and data[:2] != b"\x1f\x8b"
    members = pylib_members(out / PYLIB_TAR)
    assert [m.name for m in members] == ["PYLIB", "PYLIB/bc_parser.mpy", "PYLIB/bc_selftest.mpy", "PYLIB/VERSION"]
    assert members[0].isdir() and all(m.isfile() for m in members[1:])
    assert {(m.uid, m.gid, m.uname, m.gname, m.mtime) for m in members} == {(0, 0, "", "", 315619200)}
    with tarfile.open(out / PYLIB_TAR) as tar:
        for name, module in MODULES.items():
            f = tar.extractfile(f"PYLIB/{name}")
            assert f is not None and f.read() == module
        f = tar.extractfile("PYLIB/VERSION")
        assert f is not None
        version = f.read()
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert version == manifest["pylib_version"].encode() + b"\n" and len(manifest["pylib_version"]) == 16
    # What pylib reads at every boot without the card's tar
    assert (out / PYLIB_VER).read_bytes() == version
    again = tmp_path / "again"
    assert run(linux, buildroot, again).returncode == 0
    assert (again / PYLIB_TAR).read_bytes() == data


def test_pylib_version_follows_the_modules(tmp_path):
    """A changed module gives another version (the profile extracts the archive again), the
    same modules the same one, whatever their files' times."""
    linux, buildroot = fake_build(tmp_path)
    assert run(linux, buildroot, tmp_path / "a").returncode == 0
    os.utime(buildroot / "images" / "pylib" / "bc_parser.mpy", (1, 1))
    assert run(linux, buildroot, tmp_path / "b").returncode == 0
    write(buildroot / "images" / "pylib" / "bc_parser.mpy", b"M\x06\x00\x1fchanged")
    assert run(linux, buildroot, tmp_path / "c").returncode == 0
    a, b, c = (json.loads((tmp_path / d / "MANIFEST.json").read_text())["pylib_version"] for d in "abc")
    assert a == b != c


def test_refuses_a_pylib_file_that_is_no_module(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    write(buildroot / "images" / "pylib" / "notes.txt", b"x")
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "notes.txt is not a .mpy module" in r.stderr, r.stderr


def test_refuses_a_missing_pylib(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    for name in MODULES:
        (buildroot / "images" / "pylib" / name).unlink()
    (buildroot / "images" / "pylib").rmdir()
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "pylib" in r.stderr, r.stderr
