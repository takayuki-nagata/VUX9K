#!/bin/bash
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT
#
# Generates VUX9K's ACT4 tests: riscv-arch-test's self-checking ELFs, with the expected
# values the Sail reference model computed for this configuration (scripts/act4/vux9k/)
# built in. Runs the upstream image, so it needs docker, podman or podman-remote (set
# ACT4_ENGINE to pick one); the tests themselves then run without any of these tools.
#
# usage: generate.sh OUT_DIR      (make act4-elfs: OUT_DIR = build/act4/<key>)
set -euo pipefail

IMAGE=ghcr.io/riscv/act4:4.1.0   # riscv-arch-test 4.1.0, Sail 0.13.1
here=$(cd "$(dirname "$0")" && pwd)
out=$1

engine=${ACT4_ENGINE:-}
if [ -z "$engine" ]; then
    for e in docker podman podman-remote; do
        if command -v "$e" >/dev/null; then engine=$e; break; fi
    done
fi
[ -n "$engine" ] || { echo "generate.sh: needs docker, podman or podman-remote (ACT4_ENGINE)" >&2; exit 1; }

# Files go in and out with `cp`, not volumes, so a remote engine works the same
cid=$($engine create "$IMAGE" bash -c \
    'make -j"$(nproc)" CONFIG_FILES=config/cores/vux9k/test_config.yaml FAST=True elfs')
trap '$engine rm -f "$cid" >/dev/null' EXIT
$engine cp "$here/vux9k" "$cid:/act4/config/cores/vux9k"
start=$(date +%s)
$engine start -a "$cid"
rm -rf "$out.tmp" && mkdir -p "$out.tmp"
$engine cp "$cid:/act4/work/vux9k/elfs" "$out.tmp/elfs"
{
    echo "image $IMAGE"
    echo "generated $(date -u +%Y-%m-%dT%H:%M:%SZ) in $(( $(date +%s) - start )) s"
} > "$out.tmp/GENERATED"
rm -rf "$out" && mv "$out.tmp" "$out"
echo "ACT4 ELFs: $(find "$out/elfs" -name '*.elf' | wc -l) in $out/elfs"
