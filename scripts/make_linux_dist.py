#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Assemble the noMMU Linux kernel's distribution (make linux-dist) into a directory.

Apart from `make dist`'s tree, which needs no Linux build: the kernel `make linux` built
(upstream's arch/riscv/boot/Image with buildroot's busybox initramfs in it, and dtc's
vux9k.dtb), laid out as they go on the card (VUX9K/LINUX/, where the second stage's
`linux` finds them by default), this tree's programs for the card (VUX9K/LINUX/BIN/, from
buildroot's images/sd/, where its own packages install them: docs/agents/linux.md, "Userland
packages"), and the kernel's .config. MANIFEST.json records the
commit, the kernel's and buildroot's versions and each card file's size and sha256 (a
release's tag message carries the Image's, docs/RELEASING.md); SHA256SUMS covers every
other file. CI uploads the tree as vux9k-linux-<sha> (docs/agents/linux.md).
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_dist import REPO_ROOT, git, sha256  # noqa: E402

# What goes into the tree: its path there -> the path under --linux-out
CARD_FILES = {
    "VUX9K/LINUX/Image": "arch/riscv/boot/Image",
    "VUX9K/LINUX/vux9k.dtb": "vux9k.dtb",
}
FILES = CARD_FILES | {"linux.config": ".config"}
# The sizes MANIFEST.json records for the memory table (sim/integration/linux_checks.py): its
# key -> the path under --linux-out (the Image) or --buildroot-out
SIZES_LINUX = {"image": "arch/riscv/boot/Image"}
SIZES_BUILDROOT = {"rootfs_cpio": "images/rootfs.cpio", "busybox": "target/bin/busybox"}
# Under --buildroot-out: the card's tree this repository's buildroot packages install, copied as
# it is (its paths start with VUX9K/LINUX/BIN/)
SD_TREE = os.path.join("images", "sd")
SD_PREFIX = "VUX9K/LINUX/BIN/"
# The card's busyboxes (package vux9k-bbtools: the text tools), whose sizes the memory table
# also shows: SD_PREFIX + this prefix, the size under the file's name
BBTOOLS_PREFIX = SD_PREFIX + "bb"


def sd_files(buildroot_out: str) -> dict[str, str]:
    """images/sd/'s files: their path in the tree -> the file; exits on one outside SD_PREFIX."""
    root = os.path.join(buildroot_out, SD_TREE)
    files = {}
    for top, _, names in os.walk(root):
        for name in names:
            path = os.path.join(top, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if not rel.startswith(SD_PREFIX):
                sys.exit(f"make_linux_dist: {path} is not under {SD_PREFIX}")
            files[rel] = path
    return dict(sorted(files.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--linux-out", default=os.path.join(REPO_ROOT, "build", "linux"), help="make linux's output")
    p.add_argument(
        "--buildroot-out", default=os.path.join(REPO_ROOT, "build", "buildroot"), help="make linux-rootfs's output"
    )
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "build", "linux-dist"))
    p.add_argument("--linux-version", required=True)
    p.add_argument("--buildroot-version", required=True)
    args = p.parse_args()

    sized = {key: os.path.join(args.linux_out, src) for key, src in SIZES_LINUX.items()} | {
        key: os.path.join(args.buildroot_out, src) for key, src in SIZES_BUILDROOT.items()
    }
    inputs = [os.path.join(args.linux_out, src) for src in FILES.values()] + list(sized.values())
    missing = [path for path in inputs if not os.path.isfile(path)]
    if missing:
        listed = "\n  ".join(missing)
        sys.exit(f"make_linux_dist: missing (run `make linux-dist`):\n  {listed}")

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    for dst, src in FILES.items():
        out = os.path.join(args.out, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(args.linux_out, src), out)
        os.chmod(out, 0o644)
    bins = sd_files(args.buildroot_out)
    for dst, src in bins.items():
        out = os.path.join(args.out, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(src, out)
        os.chmod(out, 0o755)
    card = [*CARD_FILES, *bins]

    manifest = {
        "git_sha": git("rev-parse", "HEAD"),
        "git_describe": git("describe", "--tags", "--always", "--dirty"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "linux_version": args.linux_version,
        "buildroot_version": args.buildroot_version,
        "card_files": {
            dst: {"size": os.path.getsize(os.path.join(args.out, dst)), "sha256": sha256(os.path.join(args.out, dst))}
            for dst in card
        },
        "sizes": {key: os.path.getsize(path) for key, path in sized.items()}
        | {os.path.basename(dst): os.path.getsize(src) for dst, src in bins.items() if dst.startswith(BBTOOLS_PREFIX)},
    }
    with open(os.path.join(args.out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    sums = [f"{sha256(os.path.join(args.out, rel))}  {rel}" for rel in sorted([*FILES, *bins, "MANIFEST.json"])]
    with open(os.path.join(args.out, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")

    image = manifest["card_files"]["VUX9K/LINUX/Image"]
    where = os.path.relpath(args.out, REPO_ROOT)
    print(f"linux-dist: {where} ({manifest['git_describe']}, Image {image['size']} B, sha256 {image['sha256']})")


if __name__ == "__main__":
    main()
