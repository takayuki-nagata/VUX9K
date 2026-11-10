#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Assemble the noMMU Linux kernel's distribution (make linux-dist) into a directory.

Apart from `make dist`'s tree, which needs no Linux build: the kernel `make linux` built
(upstream's arch/riscv/boot/Image with buildroot's busybox initramfs in it, and dtc's
vux9k.dtb), laid out as they go on the card (VUX9K/LINUX/, where the second stage's
`linux` finds them by default), and the kernel's .config. MANIFEST.json records the
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


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--linux-out", default=os.path.join(REPO_ROOT, "build", "linux"), help="make linux's output")
    p.add_argument("--out", default=os.path.join(REPO_ROOT, "build", "linux-dist"))
    p.add_argument("--linux-version", required=True)
    p.add_argument("--buildroot-version", required=True)
    args = p.parse_args()

    missing = [src for src in FILES.values() if not os.path.isfile(os.path.join(args.linux_out, src))]
    if missing:
        listed = "\n  ".join(missing)
        sys.exit(f"make_linux_dist: missing in {args.linux_out} (run `make linux-dist`):\n  {listed}")

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    for dst, src in FILES.items():
        out = os.path.join(args.out, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(os.path.join(args.linux_out, src), out)
        os.chmod(out, 0o644)

    manifest = {
        "git_sha": git("rev-parse", "HEAD"),
        "git_describe": git("describe", "--tags", "--always", "--dirty"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "linux_version": args.linux_version,
        "buildroot_version": args.buildroot_version,
        "card_files": {
            dst: {"size": os.path.getsize(os.path.join(args.out, dst)), "sha256": sha256(os.path.join(args.out, dst))}
            for dst in CARD_FILES
        },
    }
    with open(os.path.join(args.out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    sums = [f"{sha256(os.path.join(args.out, rel))}  {rel}" for rel in sorted([*FILES, "MANIFEST.json"])]
    with open(os.path.join(args.out, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")

    image = manifest["card_files"]["VUX9K/LINUX/Image"]
    where = os.path.relpath(args.out, REPO_ROOT)
    print(f"linux-dist: {where} ({manifest['git_describe']}, Image {image['size']} B, sha256 {image['sha256']})")


if __name__ == "__main__":
    main()
