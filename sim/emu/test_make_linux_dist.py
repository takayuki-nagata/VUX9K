# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""scripts/make_linux_dist.py (make linux-dist) on fake make linux and buildroot outputs: the
card's files (Image, the device tree and the programs buildroot's packages put in images/sd/)
with their sizes and sha256 in MANIFEST.json and in SHA256SUMS, the build's sizes; refused
when images/sd/ holds a file outside VUX9K/LINUX/BIN/ or an input is missing. Needs no Linux
build."""

import hashlib
import json
import os
import subprocess
import sys

from vux9k import REPO_ROOT

SCRIPT = os.path.join(REPO_ROOT, "scripts", "make_linux_dist.py")


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
    assert manifest["sizes"] == {"image": 10, "rootfs_cpio": 7, "busybox": 5}
    card = manifest["card_files"]
    assert sorted(card) == ["VUX9K/LINUX/BIN/memprobe", "VUX9K/LINUX/Image", "VUX9K/LINUX/vux9k.dtb"]
    assert card["VUX9K/LINUX/BIN/memprobe"] == {"size": 4, "sha256": hashlib.sha256(b"M" * 4).hexdigest()}
    assert (out / "VUX9K" / "LINUX" / "BIN" / "memprobe").read_bytes() == b"M" * 4
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert sorted(sums) == sorted([*card, "MANIFEST.json", "linux.config"])
    for rel, digest in sums.items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest, rel


def test_refuses_a_card_file_outside_bin(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    write(buildroot / "images" / "sd" / "VUX9K" / "STAGE2.BIN", b"S")
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "is not under VUX9K/LINUX/BIN/" in r.stderr, r.stderr


def test_refuses_a_missing_input(tmp_path):
    linux, buildroot = fake_build(tmp_path)
    (buildroot / "target" / "bin" / "busybox").unlink()
    r = run(linux, buildroot, tmp_path / "out")
    assert r.returncode != 0 and "busybox" in r.stderr, r.stderr
