#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Assemble the application-developer distribution (make dist) into a directory.

The tree holds what an application developer needs: the bitstream, the Boot Manager
update image, the demos, vux_tool.py, the Zephyr BSP (with the Zephyr demo as a
template), the Hack demo's C sources, and the emulator (CLI and Python module).
Nothing in it depends on a version number: MANIFEST.json records the commit, the
adopted place-and-route seed, the Boot Manager version and the tool pins (taken from
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
    "boot-manager.bin": "build/firmware/firmware.bin",
    "demos/zephyr-demo.bin": "build/zephyr-demo/zephyr/zephyr.bin",
    "demos/hack-demo.bin": "build/hack/firmware.bin",
    "tools/vux_tool.py": "tools/vux_tool.py",
    "tools/requirements.txt": "tools/requirements.txt",
    "hack/main.c": "hack_demo/src/main.c",
    "hack/uart.c": "hack_demo/src/uart.c",
    "hack/uart.h": "hack_demo/src/uart.h",
    "emu/vux9k-emu": "build/emu/target/release/vux9k-emu",
    "emu/vux9k_emu.abi3.so": "build/emu/python/vux9k_emu.abi3.so",
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


def boot_manager_version():
    with open(os.path.join(REPO_ROOT, "firmware", "boot_manager", "src", "main.rs")) as f:
        m = re.search(r"const BOOT_MGR_VERSION: u32 = (\d+);", f.read())
    if not m:
        sys.exit("make_dist: BOOT_MGR_VERSION not found in firmware/boot_manager/src/main.rs")
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


def third_party_licenses(out_path):
    """License texts of the crates linked into the emulator binaries (normal deps, no proc-macros)."""
    meta = json.loads(
        subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version=1",
                "--manifest-path=emu/Cargo.toml",
                "--filter-platform=x86_64-unknown-linux-gnu",
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

    def linked_into(crate):
        """Registry crates reachable through normal dependencies; proc-macros run at build time only."""
        todo = [pid for pid, p in pkgs.items() if p["name"] == crate]
        seen = set()
        while todo:
            pid = todo.pop()
            if pid in seen or proc_macro(pid):
                continue
            seen.add(pid)
            todo += [d["pkg"] for d in nodes[pid]["deps"] if any(k["kind"] is None for k in d["dep_kinds"])]
        return sorted((pkgs[pid] for pid in seen if pkgs[pid]["source"]), key=lambda p: p["name"])

    out = [
        "Third-party software in this distribution",
        "",
        "demos/zephyr-demo.bin contains the Zephyr RTOS (https://zephyrproject.org),",
        "licensed under the Apache License 2.0; its text is the LICENSE file in",
        "zephyr/vux9k-zephyr-bsp.tar.gz. Its Rust part is built with Rust's core library",
        "(MIT OR Apache-2.0, https://github.com/rust-lang/rust).",
    ]
    linked: dict[str, dict] = {}
    for binary, crate in (("emu/vux9k-emu", "vux9k_emu_cli"), ("emu/vux9k_emu.abi3.so", "vux9k_emu_py")):
        crates = linked_into(crate)
        out += ["", f"{binary} links " + ("these Rust crates:" if crates else "no third-party Rust crates.")]
        out += [f"  {p['name']} {p['version']}  ({p['license']})" for p in crates]
        linked |= {p["id"]: p for p in crates}
    for p in sorted(linked.values(), key=lambda p: p["name"]):
        crate_dir = os.path.dirname(p["manifest_path"])
        texts = sorted(n for n in os.listdir(crate_dir) if re.match(r"(LICEN[CS]E|COPYING)", n, re.I))
        if not texts:
            sys.exit(f"make_dist: no license file in {crate_dir}")
        for name in texts:
            with open(os.path.join(crate_dir, name), errors="replace") as f:
                out += ["", "=" * 78, f"{p['name']} {p['version']}: {name}", "=" * 78, "", f.read().rstrip()]
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "build", "dist"))
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
    third_party_licenses(os.path.join(args.out, "THIRD_PARTY_LICENSES.txt"))

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
        "boot_manager_version": boot_manager_version(),
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
