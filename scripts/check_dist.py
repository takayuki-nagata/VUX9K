#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Check a `make dist` tree the way an application developer would use it.

Verifies SHA256SUMS and the manifest, then copies the tree somewhere else and runs the
demos on the shipped emulator with nothing from the repository: the CLI commands and
the Python example in docs/APP_DEVELOPMENT.md, verbatim.
"""

import argparse
import hashlib
import json
import os
import shutil
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
    "boot-manager.bin",
    "demos/zephyr-demo.bin",
    "demos/hack-demo.bin",
    "tools/vux_tool.py",
    "tools/requirements.txt",
    "hack/main.c",
    "hack/uart.c",
    "hack/uart.h",
    "emu/vux9k-emu",
    "emu/vux9k_emu.abi3.so",
    "zephyr/vux9k-zephyr-bsp.tar.gz",
]

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
]

# docs/APP_DEVELOPMENT.md's Python example; keep the two in sync
PY_EXAMPLE = """
import sys
sys.path.insert(0, "emu")
import vux9k_emu

soc = vux9k_emu.Soc("real")
with open("demos/zephyr-demo.bin", "rb") as f:
    soc.load_app(f.read(), "riscv")
end = soc.run_until_tx(b"All Rust application tasks finished successfully!", 40_000_000)
assert end is not None, soc.uart_received()
print(f"demo finished after {soc.cycle} cycles ({soc.cycle / 18e6:.2f} s at 18 MHz)")
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
    for key in ("git_sha", "git_describe", "dirty", "bitstream", "boot_manager_version", "pins"):
        if key not in m:
            fail(f"MANIFEST.json has no {key}")
    with tarfile.open(os.path.join(dist, "zephyr/vux9k-zephyr-bsp.tar.gz")) as tar:
        names = tar.getnames()
    for rel in ("zephyr/module.yml", "LICENSE", "README.md", "boards/vux9k/vux9k/board.yml", "app/CMakeLists.txt"):
        if f"vux9k-zephyr-bsp/{rel}" not in names:
            fail(f"BSP archive lacks {rel}")
    return m


def check_guide(dist):
    with open(os.path.join(dist, "docs", "APP_DEVELOPMENT.md")) as f:
        guide = f.read()
    if PY_EXAMPLE.strip() not in guide:
        fail("docs/APP_DEVELOPMENT.md's Python example differs from check_dist.PY_EXAMPLE")
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
        r = subprocess.run(
            [sys.executable, "-I", "-c", PY_EXAMPLE], cwd=copy, env=env, capture_output=True, text=True, timeout=300
        )
        if r.returncode != 0:
            fail(f"the Python example failed:\n{r.stdout}{r.stderr}")
        print(f"ok: Python example ({r.stdout.strip()})")


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
