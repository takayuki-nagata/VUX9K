#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Check a `make dist` tree the way an application developer would use it.

Verifies SHA256SUMS and the manifest, then copies the tree somewhere else and runs the
demos on the shipped emulator with nothing from the repository: the CLI commands and
the Python examples in docs/APP_DEVELOPMENT.md, verbatim (the demos loaded directly, and
a card made of sd/ booted by the first stage, whose second stage runs HELLO.BIN).
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What make_dist.py writes besides MANIFEST.json/SHA256SUMS
REQUIRED = [
    "README.md",
    "LICENSE",
    "THIRD_PARTY_LICENSES.txt",
    "docs/APP_DEVELOPMENT.md",
    "bitstream/pack.fs",
    "sd/VUX9K/STAGE2.BIN",
    "sd/VUX9K/APPS/HELLO.BIN",
    "sd/VUX9K/APPS/ZEPHYR.BIN",
    "sd/VUX9K/APPS/IRQECHO.BIN",
    "sd/VUX9K/APPS/BC.BIN",
    "sd/VUX9K/APPS/HACK.BIN",
    "demos/zephyr-demo.bin",
    "demos/hack-demo.bin",
    "tools/vux_tool.py",
    "tools/requirements.txt",
    "tools/70-vux9k-board.rules",
    "hack/main.c",
    "hack/uart.c",
    "hack/uart.h",
    "emu/vux9k-emu",
    "emu/vux9k_emu.abi3.so",
    "emu/firmware/firmware.hex",
    "emu/firmware/firmware_d0.hex",
    "emu/firmware/firmware_d1.hex",
    "emu/firmware/firmware_d2.hex",
    "emu/firmware/firmware_d3.hex",
    "zephyr/vux9k-zephyr-bsp.tar.gz",
]
# The card's v4 files and the mode (vux_tool.MODE_*: 1 RV32, 0 Hack) and load address each carries
CARD_FILES = {
    "sd/VUX9K/APPS/HELLO.BIN": (1, 0x8000_0000),
    "sd/VUX9K/APPS/ZEPHYR.BIN": (1, 0x8000_0000),
    "sd/VUX9K/APPS/IRQECHO.BIN": (1, 0x8000_0000),
    "sd/VUX9K/APPS/BC.BIN": (1, 0x8000_0000),
    "sd/VUX9K/APPS/HACK.BIN": (0, 0),
}

# Run from the dist directory (docs/APP_DEVELOPMENT.md, "Testing on the emulator")
CLI_RUNS = [
    [
        "emu/vux9k-emu",
        "--no-firmware",
        "--load",
        "demos/zephyr-demo.bin",
        "--mode",
        "riscv",
        "--until",
        "All Rust application tasks finished successfully!",
    ],
    [
        "emu/vux9k-emu",
        "--no-firmware",
        "--load",
        "demos/hack-demo.bin",
        "--mode",
        "hack",
        "--until",
        "ALL HACK C FIRMWARE TESTS PASSED",
    ],
    # A card image of sd/, booted as on the board: the first stage starts the second
    [
        "python3",
        "tools/vux_tool.py",
        "mkfat",
        "card.img",
        "--file",
        "VUX9K/STAGE2.BIN=sd/VUX9K/STAGE2.BIN",
        "--file",
        "VUX9K/APPS/HELLO.BIN=sd/VUX9K/APPS/HELLO.BIN",
    ],
    ["emu/vux9k-emu", "--firmware", "emu/firmware", "--sd", "card.img", "--until", "s2> "],
]

# docs/APP_DEVELOPMENT.md's Python example; keep the two in sync
PY_EXAMPLE = """
import sys
sys.path.insert(0, "emu")
import vux9k_emu

soc = vux9k_emu.Soc("real")
with open("demos/zephyr-demo.bin", "rb") as f:
    soc.load_app(f.read(), "riscv", 0x8000_0000)
end = soc.run_until_tx(b"All Rust application tasks finished successfully!", 40_000_000)
assert end is not None, soc.uart_received()
print(f"demo finished after {soc.cycle} cycles ({soc.cycle / 18e6:.2f} s at 18 MHz)")
"""

# docs/APP_DEVELOPMENT.md's second Python example: the board's way, from a card image
PY_CARD_EXAMPLE = r"""
import sys
sys.path[:0] = ["emu", "tools"]
import vux9k_emu
import vux_tool

files = {}
for name in ("STAGE2.BIN", "APPS/HELLO.BIN"):
    with open(f"sd/VUX9K/{name}", "rb") as f:
        files[f"VUX9K/{name}"] = f.read()
soc = vux9k_emu.Soc("real")
hexes = ["firmware.hex"] + [f"firmware_d{i}.hex" for i in range(4)]
soc.load_readmemh(*(open(f"emu/firmware/{name}").read() for name in hexes))
soc.sd_insert(vux_tool.build_fat32_image(files))
assert soc.run_until_tx(b"s2> ", 40_000_000) is not None, soc.uart_received()
soc.uart_send(b"run VUX9K/APPS/HELLO.BIN\r")
assert soc.run_until_tx(b"for the first stage.\n", 20_000_000) is not None, soc.uart_received()
print(soc.uart_received().decode().splitlines()[-2])
"""


def fail(msg):
    sys.exit(f"check_dist: FAIL: {msg}")


def check_sums(dist):
    with open(os.path.join(dist, "SHA256SUMS")) as f:
        listed: dict[str, str] = {}
        for line in f:
            if line.strip():
                digest, name = line.rstrip("\n").split("  ", 1)
                listed[name] = digest
    present = set()
    for root, _, names in os.walk(dist):
        for name in names:
            present.add(os.path.relpath(os.path.join(root, name), dist))
    present.discard("SHA256SUMS")
    if set(listed) != present:
        fail(f"SHA256SUMS lists {sorted(set(listed) ^ present)} differently from the tree")
    for rel, digest in listed.items():
        with open(os.path.join(dist, rel), "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != digest:
                fail(f"{rel}: sha256 mismatch")
    for rel in REQUIRED + ["MANIFEST.json"]:
        if rel not in present:
            fail(f"{rel} missing")


def check_manifest(dist):
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        m = json.load(f)
    for key in ("git_sha", "git_describe", "dirty", "bitstream", "stage1_version", "stage2_version", "pins"):
        if key not in m:
            fail(f"MANIFEST.json has no {key}")
    # v4 headers: magic, header version, reserved, mode, size, load address, entry, crc, version
    for rel, (mode, load) in {**CARD_FILES, "sd/VUX9K/STAGE2.BIN": (1, 0x8070_0000)}.items():
        with open(os.path.join(dist, rel), "rb") as f:
            data = f.read()
        magic, _, _, got_mode, size, got_load, _, _, version = struct.unpack_from("<IHHIIIIII", data)
        if magic != 0x56555839 or (got_mode, got_load) != (mode, load) or size != len(data) - 64:
            fail(f"{rel}: not a v4 file with mode {mode} at {load:#x}")
        if rel.endswith("STAGE2.BIN") and version != m["stage2_version"]:
            fail(f"{rel} is v{version}, MANIFEST.json's stage2_version {m['stage2_version']}")
    with tarfile.open(os.path.join(dist, "zephyr/vux9k-zephyr-bsp.tar.gz")) as tar:
        names = tar.getnames()
    for rel in ("zephyr/module.yml", "LICENSE", "README.md", "boards/vux9k/vux9k/board.yml", "app/CMakeLists.txt"):
        if f"vux9k-zephyr-bsp/{rel}" not in names:
            fail(f"BSP archive lacks {rel}")
    return m


def check_guide(dist):
    with open(os.path.join(dist, "docs", "APP_DEVELOPMENT.md")) as f:
        guide = f.read()
    for name, example in (("PY_EXAMPLE", PY_EXAMPLE), ("PY_CARD_EXAMPLE", PY_CARD_EXAMPLE)):
        if example.strip() not in guide:
            fail(f"docs/APP_DEVELOPMENT.md's Python example differs from check_dist.{name}")
    if "](../README.md" in guide:
        fail("docs/APP_DEVELOPMENT.md still links ../README.md (make_dist rewrites those)")


def run_demos(dist):
    with tempfile.TemporaryDirectory(prefix="vux9k-dist-") as tmp:
        copy = os.path.join(tmp, "dist")
        shutil.copytree(dist, copy, symlinks=False)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp}
        for cmd in CLI_RUNS:
            r = subprocess.run(cmd, cwd=copy, env=env, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                fail(f"{' '.join(cmd)} exited {r.returncode}:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
            print(f"ok: {' '.join(cmd[:6])}")
        for name, example in (("PY_EXAMPLE", PY_EXAMPLE), ("PY_CARD_EXAMPLE", PY_CARD_EXAMPLE)):
            r = subprocess.run(
                [sys.executable, "-I", "-c", example], cwd=copy, env=env, capture_output=True, text=True, timeout=300
            )
            if r.returncode != 0:
                fail(f"the Python example {name} failed:\n{r.stdout}{r.stderr}")
            print(f"ok: Python example {name} ({r.stdout.strip()})")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("dist", nargs="?", default=os.path.join(REPO_ROOT, "build", "dist"))
    args = p.parse_args()
    check_sums(args.dist)
    m = check_manifest(args.dist)
    check_guide(args.dist)
    run_demos(args.dist)
    print(f"check_dist: OK ({m['git_describe']}, seed {m['bitstream']['pnr_seed']})")


if __name__ == "__main__":
    main()
