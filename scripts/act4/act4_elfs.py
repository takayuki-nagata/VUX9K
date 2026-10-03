#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Where VUX9K's ACT4 ELFs are, and making them (`make act4-elfs`).

The ELFs depend only on generate.sh (it names the upstream image, hence the riscv-arch-test
and Sail versions) and the configuration in scripts/act4/vux9k/, so they live in
build/act4/<key>/ with key = a hash of those files: changing the configuration (say,
adding an extension) makes new ones, and an unchanged configuration reuses them (CI
caches build/act4/ under the same key). If ACT4_ELF_CACHE names a directory holding
<key>/ already (generated elsewhere, e.g. by a CI host that can run containers when the
job can't), ensure() copies it from there instead of generating.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
ACT4_BUILD = REPO_ROOT / "build" / "act4"


def key():
    h = hashlib.sha256()
    for path in [HERE / "generate.sh", *sorted((HERE / "vux9k").iterdir())]:
        h.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def out_dir():
    return ACT4_BUILD / key()


def elfs():
    """The generated ELFs, sorted, or [] if they haven't been generated."""
    return sorted((out_dir() / "elfs").rglob("*.elf"))


def ensure():
    """Generate the ELFs for the current configuration unless they exist."""
    if elfs():
        return
    cache = os.environ.get("ACT4_ELF_CACHE")
    if cache and (Path(cache) / key() / "elfs").is_dir():
        shutil.copytree(Path(cache) / key(), out_dir(), dirs_exist_ok=True)
        return
    subprocess.run([str(HERE / "generate.sh"), str(out_dir())], check=True)
    if not elfs():
        sys.exit(f"act4: generate.sh made no ELFs in {out_dir()}")


if __name__ == "__main__":
    if sys.argv[1:] == ["key"]:
        print(key())
    elif sys.argv[1:] == ["dir"]:
        print(out_dir().relative_to(REPO_ROOT))
    else:
        ensure()
        print(f"{len(elfs())} ACT4 ELFs in {out_dir().relative_to(REPO_ROOT)}")
