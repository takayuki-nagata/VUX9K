#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Tie a GitHub Release to the bitstream that was tested on the board.

A release is the `make dist` tree CI built for a commit, published only if it is the
one the maintainer put on the board. The evidence travels in the annotated tag:

  template DIR       print a tag message for that dist, with the board results to fill in
  verify DIR MSG     check a dist against a tag message (the release workflow runs this):
                     SHA256SUMS, clean build of the tagged commit, pack.fs's hash equal to
                     the one in the message, and every result filled in
  selftest DIR       template -> verify round trip, and verify must reject a wrong hash

docs/RELEASING.md has the procedure.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

FILL = "<fill in>"
# Tag message keys: (key, source); None = the maintainer fills it in
FIELDS = [
    ("commit", "git_sha"),
    ("pack.fs sha256", "pack_sha256"),
    ("pnr seed", "seed"),
    ("boot manager version", "boot_manager_version"),
    ("test-hw", None),
    ("hw-smoke", None),
]


class Mismatch(Exception):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_dist(dist):
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        m = json.load(f)
    with open(os.path.join(dist, "SHA256SUMS")) as f:
        for line in f:
            if line.strip():
                digest, rel = line.rstrip("\n").split("  ", 1)
                if sha256(os.path.join(dist, rel)) != digest:
                    raise Mismatch(f"{rel} doesn't match SHA256SUMS")
    b = m["bitstream"]
    return m, {
        "git_sha": m["git_sha"],
        "pack_sha256": sha256(os.path.join(dist, b["file"])),
        "seed": f"{b['pnr_seed']} (STA slack {b['sta_slack_ns']:+.3f} ns at {b['sta_target_mhz']} MHz)",
        "boot_manager_version": str(m["boot_manager_version"]),
    }


def template(dist, version="vX.Y.Z"):
    _, facts = load_dist(dist)
    lines = [version, ""]
    for key, src in FIELDS:
        lines.append(f"{key}: {facts[src] if src else FILL}")
    lines += [
        "",
        "Fill in test-hw from `make test-hw-dist DIST=<this dist>` on the board (e.g. 15/15 PASS)",
        "and hw-smoke from `make timing hw-smoke` at this commit (e.g. 5/5 seeds 11/11).",
    ]
    return "\n".join(lines) + "\n"


def parse(message):
    fields = {}
    for line in message.splitlines():
        m = re.match(r"([a-z][a-z0-9. -]*):\s*(.*)$", line)
        if m and m.group(1) in dict(FIELDS) and m.group(1) not in fields:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def verify(dist, message, commit, allow_dirty=False):
    m, facts = load_dist(dist)
    if m["dirty"] and not allow_dirty:
        raise Mismatch("the dist was built from a tree with uncommitted changes")
    if m["git_sha"] != commit:
        raise Mismatch(f"the dist is of {m['git_sha']}, the tag points to {commit}")
    fields = parse(message)
    for key, _ in FIELDS:
        value = fields.get(key, "")
        if not value or FILL in value:
            raise Mismatch(f"tag message: '{key}' is missing or not filled in")
        if key == "commit" and not commit.startswith(value):
            raise Mismatch(f"tag message names commit {value}, the tag points to {commit}")
        if key == "pack.fs sha256" and value != facts["pack_sha256"]:
            raise Mismatch(
                f"pack.fs is {facts['pack_sha256']}, the tag message says {value}: "
                "this is not the bitstream that was tested"
            )
    return m


def selftest(dist):
    m, facts = load_dist(dist)
    filled = template(dist, "v0.0.0-selftest").replace(FILL, "selftest")
    verify(dist, filled, m["git_sha"], allow_dirty=True)
    bad = facts["pack_sha256"][:-1] + ("0" if facts["pack_sha256"][-1] != "0" else "1")
    for broken, why in (
        (filled.replace(facts["pack_sha256"], bad), "a different pack.fs hash"),
        (template(dist), "unfilled results"),
    ):
        try:
            verify(dist, broken, m["git_sha"], allow_dirty=True)
        except Mismatch:
            continue
        raise Mismatch(f"selftest: verify accepted {why}")
    # A dist whose files changed after make_dist must fail too
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "dist")
        os.makedirs(copy)
        for name in ("MANIFEST.json", "SHA256SUMS"):
            with open(os.path.join(dist, name), "rb") as src, open(os.path.join(copy, name), "wb") as dst:
                dst.write(src.read())
        try:
            load_dist(copy)
        except (Mismatch, FileNotFoundError):
            pass
        else:
            raise Mismatch("selftest: a dist with missing files passed")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    t.add_argument("dist")
    t.add_argument("--version", default="vX.Y.Z")
    v = sub.add_parser("verify")
    v.add_argument("dist")
    v.add_argument("message", help="file with the annotated tag's message")
    v.add_argument("--commit", required=True, help="full SHA the tag points to")
    s = sub.add_parser("selftest")
    s.add_argument("dist")
    args = p.parse_args()

    try:
        if args.cmd == "template":
            sys.stdout.write(template(args.dist, args.version))
        elif args.cmd == "verify":
            with open(args.message) as f:
                m = verify(args.dist, f.read(), args.commit)
            print(f"release_check: OK ({m['git_sha']}, pack.fs as tested on the board)")
        else:
            selftest(args.dist)
            print("release_check: selftest OK")
    except Mismatch as e:
        sys.exit(f"release_check: FAIL: {e}")


if __name__ == "__main__":
    main()
