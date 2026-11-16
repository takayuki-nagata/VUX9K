#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Assemble the noMMU Linux kernel's distribution (make linux-dist) into a directory.

Apart from `make dist`'s tree, which needs no Linux build: the kernel `make linux` built
(upstream's arch/riscv/boot/Image with buildroot's busybox initramfs in it, and dtc's
vux9k.dtb), laid out as they go on the card (VUX9K/LINUX/, where the second stage's
`linux` finds them by default), this tree's programs for the card (VUX9K/LINUX/BIN/, from
buildroot's images/sd/, where its own packages install them: docs/agents/linux.md, "Userland
packages"), MicroPython's modules as one archive for the card (VUX9K/LINUX/PYLIB.TAR, made here
from images/pylib/, with its version beside it in VUX9K/LINUX/PYLIB.VER: docs/agents/linux.md,
"MicroPython"), and the kernel's .config. MANIFEST.json records the
commit, the kernel's and buildroot's versions and each card file's size and sha256 (a
release's tag message carries the Image's, docs/RELEASING.md); SHA256SUMS covers every
other file. CI uploads the tree as vux9k-linux-<sha> (docs/agents/linux.md).
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile

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
SIZES_BUILDROOT = {
    "rootfs_cpio": "images/rootfs.cpio",
    "busybox": "target/bin/busybox",
    # MicroPython on the card (package vux9k-micropython): its exec's block is order 7
    "mpy": "images/sd/VUX9K/LINUX/BIN/MPY",
}
# Under --buildroot-out: the card's tree this repository's buildroot packages install, copied as
# it is (its paths start with VUX9K/LINUX/BIN/)
SD_TREE = os.path.join("images", "sd")
SD_PREFIX = "VUX9K/LINUX/BIN/"
# The card's busyboxes (package vux9k-bbtools: the text tools), whose sizes the memory table
# also shows: SD_PREFIX + this prefix, the size under the file's name. The other files there
# (memprobe, the scripts of vux9k-scripts) are only in card_files
BBTOOLS_PREFIX = SD_PREFIX + "bb"
# Under --buildroot-out: MicroPython's compiled modules (package vux9k-micropython), only .mpy
# files, which go to the card as one archive, PYLIB_TAR: the stages and mkfat write 8.3 names
# only, and the modules' are longer. The login shell's profile extracts it into PYLIB_MEMBER/
# beside it (linux_workspace/userland/scripts/pylib) when its PYLIB_VERSION, the last member,
# is not the one there. The same version is also a card file of its own, PYLIB_VER, which pylib
# reads with the shell's builtins at every boot: reading it out of the archive would execute the
# card's tar each time. An uncompressed ustar archive (the card's bbtar has no gzip), made
# here with deterministic metadata: the names sorted, owner 0, no user or group names, the
# same time on every member (TAR_MTIME, a day after FAT's epoch of 1980, which no time zone
# puts before it), so the same modules give the same bytes.
PYLIB_SRC = os.path.join("images", "pylib")
PYLIB_TAR = "VUX9K/LINUX/PYLIB.TAR"
PYLIB_MEMBER = "PYLIB"
PYLIB_VERSION = PYLIB_MEMBER + "/VERSION"
PYLIB_VER = "VUX9K/LINUX/PYLIB.VER"
TAR_MTIME = 315619200


def is_script(path: str) -> bool:
    """A #! script (package vux9k-scripts), not a FLAT executable."""
    with open(path, "rb") as f:
        return f.read(2) == b"#!"


def sd_files(buildroot_out: str) -> dict[str, str]:
    """images/sd/'s files: their path in the tree -> the file; exits on one outside SD_PREFIX,
    and on a script with a CR (CR LF lines: its #! line would name an interpreter that ends in CR,
    which the kernel doesn't find)."""
    root = os.path.join(buildroot_out, SD_TREE)
    files = {}
    for top, _, names in os.walk(root):
        for name in names:
            path = os.path.join(top, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if not rel.startswith(SD_PREFIX):
                sys.exit(f"make_linux_dist: {path} is not under {SD_PREFIX}")
            if is_script(path):
                with open(path, "rb") as f:
                    if b"\r" in f.read():
                        sys.exit(f"make_linux_dist: {path} is a script with CR LF lines")
            files[rel] = path
    return dict(sorted(files.items()))


def pylib_files(buildroot_out: str) -> dict[str, str]:
    """images/pylib/'s modules: their name -> the file; exits on anything but a .mpy file."""
    root = os.path.join(buildroot_out, PYLIB_SRC)
    files = {}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not name.endswith(".mpy") or not os.path.isfile(path):
            sys.exit(f"make_linux_dist: {path} is not a .mpy module")
        files[name] = path
    if not files:
        sys.exit(f"make_linux_dist: no .mpy module in {root}")
    return files


def pylib_version(files: dict[str, str]) -> str:
    """What PYLIB_VERSION holds: the first 16 hex digits of a sha256 over each module's name
    and contents, in name order (the same modules, the same version)."""
    h = hashlib.sha256()
    for name, path in files.items():
        h.update(f"{name}\0{sha256(path)}\n".encode())
    return h.hexdigest()[:16]


def write_pylib_tar(files: dict[str, str], out: str) -> str:
    """The card's PYLIB_TAR at out: PYLIB_MEMBER/, its modules, then PYLIB_VERSION (last, so an
    extraction cut short leaves none and is done again); returns the version."""
    version = pylib_version(files)

    def member(name: str, kind: bytes, size: int = 0) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = size
        info.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
        info.mtime = TAR_MTIME
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(out, "w", format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(member(PYLIB_MEMBER, tarfile.DIRTYPE))
        for name, path in files.items():
            with open(path, "rb") as f:
                tar.addfile(member(f"{PYLIB_MEMBER}/{name}", tarfile.REGTYPE, os.path.getsize(path)), f)
        data = f"{version}\n".encode()
        tar.addfile(member(PYLIB_VERSION, tarfile.REGTYPE, len(data)), io.BytesIO(data))
    return version


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
    if not os.path.isdir(os.path.join(args.buildroot_out, PYLIB_SRC)):
        missing.append(os.path.join(args.buildroot_out, PYLIB_SRC) + os.sep)
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
    pylib = os.path.join(args.out, PYLIB_TAR)
    version = write_pylib_tar(pylib_files(args.buildroot_out), pylib)
    os.chmod(pylib, 0o644)
    with open(os.path.join(args.out, PYLIB_VER), "w") as f:
        f.write(f"{version}\n")
    os.chmod(os.path.join(args.out, PYLIB_VER), 0o644)
    card = [*CARD_FILES, *bins, PYLIB_TAR, PYLIB_VER]

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
        | {os.path.basename(dst): os.path.getsize(src) for dst, src in bins.items() if dst.startswith(BBTOOLS_PREFIX)}
        | {"pylib_tar": os.path.getsize(pylib)},
        "pylib_version": version,
    }
    with open(os.path.join(args.out, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    sums = [
        f"{sha256(os.path.join(args.out, rel))}  {rel}"
        for rel in sorted([*FILES, *bins, PYLIB_TAR, PYLIB_VER, "MANIFEST.json"])
    ]
    with open(os.path.join(args.out, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")

    image = manifest["card_files"]["VUX9K/LINUX/Image"]
    where = os.path.relpath(args.out, REPO_ROOT)
    print(f"linux-dist: {where} ({manifest['git_describe']}, Image {image['size']} B, sha256 {image['sha256']})")


if __name__ == "__main__":
    main()
