#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Assemble the application-developer distribution (make dist) into a directory.

The tree holds what an application developer needs: the bitstream (with the first stage
in it), the card's files (sd/VUX9K/: the second stage, STAGE2.BIN, and the demos as v4
files in APPS/, which its `run` starts), the demos' raw images for the emulator (demos/),
vux_tool.py, the Zephyr BSP (with the Zephyr demo as a template), the Hack demo's C
sources, and the emulator (CLI and Python module, with the first stage's memory images in
emu/firmware/ to boot a card image as the board does). Nothing in it depends on a version
number: MANIFEST.json records the commit, the adopted place-and-route seed, the first and
second stages' versions and the tool pins (taken from
.github/workflows/ci.yml, so they can't drift from what CI builds with), and the
release workflow names the archives after the tag. SHA256SUMS covers every other file.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# dist path -> source path (relative to the repo root)
FILES = {
    "README.md": "docs/dist-README.md",
    "docs/APP_DEVELOPMENT.md": "docs/APP_DEVELOPMENT.md",
    "LICENSE": "LICENSE",
    "bitstream/pack.fs": "build/synth/pack.fs",
    "sd/VUX9K/STAGE2.BIN": "build/firmware/stage2/STAGE2.BIN",
    "sd/VUX9K/APPS/HELLO.BIN": "build/firmware/apps/HELLO.BIN",
    "sd/VUX9K/APPS/ZEPHYR.BIN": "build/firmware/apps/ZEPHYR.BIN",
    "sd/VUX9K/APPS/IRQECHO.BIN": "build/firmware/apps/IRQECHO.BIN",
    "sd/VUX9K/APPS/BC.BIN": "build/firmware/apps/BC.BIN",
    "sd/VUX9K/APPS/MPY.BIN": "build/firmware/apps/MPY.BIN",
    "sd/VUX9K/APPS/HACK.BIN": "build/firmware/apps/HACK.BIN",
    "demos/zephyr-demo.bin": "build/zephyr-demo/zephyr/zephyr.bin",
    "demos/hack-demo.bin": "build/hack/firmware.bin",
    "tools/vux_tool.py": "tools/vux_tool.py",
    "tools/requirements.txt": "tools/requirements.txt",
    "tools/70-vux9k-board.rules": "tools/70-vux9k-board.rules",
    "hack/main.c": "hack_demo/src/main.c",
    "hack/uart.c": "hack_demo/src/uart.c",
    "hack/uart.h": "hack_demo/src/uart.h",
    "emu/vux9k-emu": "build/emu/target/release/vux9k-emu",
    "emu/vux9k_emu.abi3.so": "build/emu/python/vux9k_emu.abi3.so",
    # The boot ROM as the bitstream holds it (the first stage), for --firmware emu/firmware
    "emu/firmware/firmware.hex": "build/firmware/firmware.hex",
}
# docs/APP_DEVELOPMENT.md links the repository's README as ../README.md; in the dist that
# path is the dist's own README, so those links go to the README of the source commit
REPO_URL = "https://github.com/takayuki-nagata/VUX9K"
EXECUTABLE = {"emu/vux9k-emu", "tools/vux_tool.py"}
BSP_ARCHIVE = "zephyr/vux9k-zephyr-bsp.tar.gz"
BSP_PREFIX = "vux9k-zephyr-bsp"

# name -> regex over ci.yml; group 1 is the value
PINS = {
    "zephyr": r"--mr (v[0-9.]+)",
    "zephyr_sdk": r"zephyr-sdk-([0-9.]+)_linux",
    "rust": r'toolchain: "([0-9.]+)"',
    "hack_tools": r"hack_tools/releases/download/(v[0-9.]+)/has-",
    "msp430_gcc": r"msp430-gcc-([0-9.]+)_linux64",
    "oss_cad_suite": r"setup-oss-cad-suite@v\d+\s+with:\s+version: '([0-9-]+)'",
}


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool_pins():
    with open(os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")) as f:
        ci = f.read()
    pins = {}
    for name, pattern in PINS.items():
        m = re.search(pattern, ci)
        if not m:
            sys.exit(f"make_dist: can't find the {name} pin in ci.yml ({pattern})")
        pins[name] = m.group(1)
    return pins


def stage_version(n):
    """STAGE1_VERSION or STAGE2_VERSION from firmware/stage<n>/src/main.rs."""
    with open(os.path.join(REPO_ROOT, "firmware", f"stage{n}", "src", "main.rs")) as f:
        m = re.search(rf"const STAGE{n}_VERSION: u32 = (\d+);", f.read())
    if not m:
        sys.exit(f"make_dist: STAGE{n}_VERSION not found in firmware/stage{n}/src/main.rs")
    return int(m.group(1))


def bsp_archive(out_path, mtime):
    """zephyr_workspace/'s tracked files as a reproducible tar.gz (fixed order, owner, mtime)."""
    names = sorted(git("ls-files", "zephyr_workspace").splitlines())
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in names:
            src = os.path.join(REPO_ROOT, name)
            info = tar.gettarinfo(src, arcname=BSP_PREFIX + name[len("zephyr_workspace") :])
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = mtime
            info.mode = 0o755 if os.access(src, os.X_OK) else 0o644
            with open(src, "rb") as f:
                tar.addfile(info, f)
    with open(out_path, "wb") as out, gzip.GzipFile(fileobj=out, mode="wb", mtime=mtime, filename="") as gz:
        gz.write(buf.getvalue())


# Binaries that link registry crates: dist path -> (Cargo manifest, target, extra cargo
# metadata flags, the crate that is linked). bc's flags are the Makefile's zephyr-bc-lib
# build (the Zephyr app's CMake builds bc_zephyr the same way)
LINKED_CRATES = {
    "emu/vux9k-emu": ("emu/Cargo.toml", "x86_64-unknown-linux-gnu", [], "vux9k_emu_cli"),
    "emu/vux9k_emu.abi3.so": ("emu/Cargo.toml", "x86_64-unknown-linux-gnu", [], "vux9k_emu_py"),
    "sd/VUX9K/APPS/BC.BIN": (
        "vendor/bc_clone_rs/Cargo.toml",
        "riscv32i-unknown-none-elf",
        ["--no-default-features"],
        "bc_zephyr",
    ),
}


def linked_into(manifest, platform, flags, crate):
    """Registry crates reachable from crate through normal dependencies; proc-macros run at
    build time only."""
    meta = json.loads(
        subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version=1",
                f"--manifest-path={manifest}",
                f"--filter-platform={platform}",
                *flags,
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    pkgs = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}

    def proc_macro(pid):
        return any("proc-macro" in t["kind"] for t in pkgs[pid]["targets"])

    todo = [pid for pid, p in pkgs.items() if p["name"] == crate]
    seen = set()
    while todo:
        pid = todo.pop()
        if pid in seen or proc_macro(pid):
            continue
        seen.add(pid)
        todo += [d["pkg"] for d in nodes[pid]["deps"] if any(k["kind"] is None for k in d["dep_kinds"])]
    return sorted((pkgs[pid] for pid in seen if pkgs[pid]["source"]), key=lambda p: p["name"])


def offers_apache(expr):
    """Whether the SPDX license expression lets the crate be used under Apache-2.0 alone
    ("MIT OR Apache-2.0", the older "MIT/Apache-2.0")."""
    return "Apache-2.0" in (a.strip(" ()") for a in re.split(r"\s+OR\s+|/", expr or ""))


def third_party_licenses(out_path, micropython_dir):
    """License texts of the crates linked into the emulator binaries and bc (normal deps,
    no proc-macros), and MicroPython's (from the tree it was built from)."""
    out = [
        "Third-party software in this distribution",
        "",
        "demos/zephyr-demo.bin and sd/VUX9K/APPS/ZEPHYR.BIN, IRQECHO.BIN, BC.BIN and MPY.BIN contain",
        "the Zephyr RTOS (https://zephyrproject.org), licensed under the Apache License 2.0;",
        "its text is the LICENSE file in zephyr/vux9k-zephyr-bsp.tar.gz. The Zephyr demo's",
        "Rust part, sd/VUX9K/STAGE2.BIN and sd/VUX9K/APPS/HELLO.BIN are built with Rust's",
        "core library (MIT OR Apache-2.0, https://github.com/rust-lang/rust). BC.BIN's Rust",
        "part is bc_clone_rs (MIT, the same author), built with Rust's core and alloc",
        "libraries and the crates listed below. MPY.BIN's Python part is",
        f"{os.path.basename(micropython_dir)} (MIT, https://micropython.org; its port to",
        "Zephyr, built with no lib/ component), whose LICENSE file is below.",
    ]
    linked: dict[str, dict] = {}
    for binary, (manifest, platform, flags, crate) in LINKED_CRATES.items():
        crates = linked_into(manifest, platform, flags, crate)
        out += ["", f"{binary} links " + ("these Rust crates:" if crates else "no third-party Rust crates.")]
        out += [f"  {p['name']} {p['version']}  ({p['license']})" for p in crates]
        linked |= {p["id"]: p for p in crates}
    unshipped = []  # crates without a license file, offered under Apache-2.0
    apache_text = None  # (crate, file) whose text is the Apache License 2.0
    for p in sorted(linked.values(), key=lambda p: p["name"]):
        crate_dir = os.path.dirname(p["manifest_path"])
        texts = sorted(n for n in os.listdir(crate_dir) if re.match(r"(LICEN[CS]E|COPYING)", n, re.I))
        if not texts:
            if not offers_apache(p["license"]):
                sys.exit(f"make_dist: no license file in {crate_dir}, and {p['license']!r} offers no Apache-2.0")
            unshipped.append(p)
        for name in texts:
            with open(os.path.join(crate_dir, name), errors="replace") as f:
                text = f.read().rstrip()
            if apache_text is None and "apache" in name.lower() and text.lstrip().startswith("Apache License"):
                apache_text = (f"{p['name']} {p['version']}", name)
            out += ["", "=" * 78, f"{p['name']} {p['version']}: {name}", "=" * 78, "", text]
    with open(os.path.join(REPO_ROOT, micropython_dir, "LICENSE")) as f:
        out += ["", "=" * 78, f"{os.path.basename(micropython_dir)}: LICENSE", "=" * 78, "", f.read().rstrip()]
    if unshipped:
        if apache_text is None:
            names = ", ".join(f"{p['name']} {p['version']}" for p in unshipped)
            sys.exit(f"make_dist: no license file in {names}, and no Apache License 2.0 text to refer to")
        out += ["", "=" * 78, "Crates without a license file", "=" * 78, ""]
        out += [
            f"{p['name']} {p['version']} ships no license file; it is offered under {p['license']} "
            f"(Cargo.toml) and is distributed here under Apache-2.0, whose text is above under "
            f"{apache_text[0]}: {apache_text[1]}."
            for p in unshipped
        ]
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "build", "dist"))
    p.add_argument("--micropython-dir", required=True, help="the MicroPython tree MPY.BIN was built from")
    args = p.parse_args()

    missing = [src for src in FILES.values() if not os.path.isfile(os.path.join(REPO_ROOT, src))]
    if missing:
        sys.exit("make_dist: missing inputs (run `make dist`, not this script alone):\n  " + "\n  ".join(missing))

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    for dst, src in FILES.items():
        out = os.path.join(args.out, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(REPO_ROOT, src), out)
        os.chmod(out, 0o755 if dst in EXECUTABLE else 0o644)

    sha = git("rev-parse", "HEAD")
    guide = os.path.join(args.out, "docs", "APP_DEVELOPMENT.md")
    with open(guide) as f:
        text = f.read().replace("](../README.md", f"]({REPO_URL}/blob/{sha}/README.md")
    with open(guide, "w") as f:
        f.write(text)

    commit_time = int(git("log", "-1", "--format=%ct"))
    bsp_archive(os.path.join(args.out, BSP_ARCHIVE), commit_time)
    third_party_licenses(os.path.join(args.out, "THIRD_PARTY_LICENSES.txt"), args.micropython_dir)

    with open(os.path.join(REPO_ROOT, "build", "synth", "pnr_seed.json")) as f:
        pnr = json.load(f)
    manifest = {
        "git_sha": sha,
        "git_describe": git("describe", "--tags", "--always", "--dirty"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "bitstream": {
            "file": "bitstream/pack.fs",
            "pnr_seed": pnr["seed"],
            "sta_slack_ns": pnr["slack_ns"],
            "sta_target_mhz": pnr["target_mhz"],
            "sta_closure": pnr["closure"],
        },
        "stage1_version": stage_version(1),
        "stage2_version": stage_version(2),
        "pins": tool_pins(),
    }
    with open(os.path.join(args.out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    sums = []
    for root, _, names in os.walk(args.out):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), args.out)
            if rel != "SHA256SUMS":
                sums.append(f"{sha256(os.path.join(root, name))}  {rel}")
    with open(os.path.join(args.out, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sorted(sums, key=lambda line: line[66:])) + "\n")

    print(f"dist: {os.path.relpath(args.out, REPO_ROOT)} ({manifest['git_describe']}, seed {pnr['seed']})")


if __name__ == "__main__":
    main()
