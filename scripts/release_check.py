#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Tie a GitHub Release to the bitstream that was tested on the board.

A release is the `make dist` tree CI built for a commit, with the Linux kernel CI built
for it (`make linux-dist`), published only if they are the ones the maintainer put on the
board. The evidence travels in the annotated tag:

  template DIR --linux LDIR
                     print a tag message for that dist and kernel, with the board results,
                     the compatibility line and the release notes to fill in
  verify DIR MSG --linux LDIR
                     check a dist and a kernel against a tag message (the release workflow
                     runs this): SHA256SUMS, clean builds of the tagged commit, pack.fs's and
                     the Image's hashes equal to the ones in the message, everything filled in
  notes MSG          print the release notes for a tag message (the release workflow
                     publishes them): the fields in a code block, the notes as Markdown
  selftest DIR       template -> verify round trip, and verify must reject a wrong hash

A tag message is paragraphs separated by blank lines: the version, the fields
(`key: value`, one per line), then the release notes in Markdown.

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
NOTES_FILL = "<fill in: release notes (Markdown)>"
# Tag message keys: (key, source); None = the maintainer fills it in
FIELDS = [
    ("commit", "git_sha"),
    ("pack.fs sha256", "pack_sha256"),
    ("pnr seed", "seed"),
    ("first stage version", "stage1_version"),
    ("second stage version", "stage2_version"),
    ("linux image sha256", "image_sha256"),
    ("test-hw", None),
    ("hw-smoke", None),
    ("test-hw-upgrade", None),
    ("test-hw-linux", None),
    ("compatibility", None),
]


class Mismatch(Exception):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_sums(dist):
    """Every file SHA256SUMS lists has its hash (any release's dist, older ones too)."""
    with open(os.path.join(dist, "SHA256SUMS")) as f:
        for line in f:
            if line.strip():
                digest, rel = line.rstrip("\n").split("  ", 1)
                if sha256(os.path.join(dist, rel)) != digest:
                    raise Mismatch(f"{rel} doesn't match SHA256SUMS")


def load_dist(dist):
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        m = json.load(f)
    check_sums(dist)
    b = m["bitstream"]
    return m, {
        "git_sha": m["git_sha"],
        "pack_sha256": sha256(os.path.join(dist, b["file"])),
        "seed": f"{b['pnr_seed']} (STA slack {b['sta_slack_ns']:+.3f} ns at {b['sta_target_mhz']} MHz)",
        "stage1_version": str(m["stage1_version"]),
        "stage2_version": str(m["stage2_version"]),
    }


# make linux-dist's tree: the Image as it goes on the card
LINUX_IMAGE = "VUX9K/LINUX/Image"


def load_linux(linux):
    """make linux-dist's MANIFEST and the Image's sha256 (SHA256SUMS checked first)."""
    with open(os.path.join(linux, "MANIFEST.json")) as f:
        m = json.load(f)
    check_sums(linux)
    return m, sha256(os.path.join(linux, LINUX_IMAGE))


def template(dist, linux, version="vX.Y.Z"):
    _, facts = load_dist(dist)
    facts["image_sha256"] = load_linux(linux)[1]
    lines = [version, ""]
    for key, src in FIELDS:
        lines.append(f"{key}: {facts[src] if src else FILL}")
    lines += ["", NOTES_FILL]
    return "\n".join(lines) + "\n"


TEMPLATE_HELP = """\
Fill in, from the board (docs/RELEASING.md, step 2):
  test-hw          `make test-hw-dist DIST=<this dist>`, e.g. 16/16 PASS
  hw-smoke         `make timing hw-smoke` at this commit, e.g. 5/5 seeds 16/16
  test-hw-upgrade  `make test-hw-upgrade DIST=<this dist>` (the previous release's bitstream,
                   then this one), e.g. 2/2 PASS from v0.3.0
  test-hw-linux    `make test-hw-linux-dist DIST=<this dist> LINUX=<this kernel>`, e.g.
                   PASS, 26 s from the bitstream's load to busybox's prompt
  compatibility    whether the previous release's applications and SD cards still work;
                   if not, why the minor or major version went up
and replace the last line with the release notes in Markdown.
"""


# A signed tag's %(contents) ends with its signature; it is not part of the notes
SIGNATURE = re.compile(r"^-----BEGIN [A-Z ]*SIGNATURE-----$")


def split(message):
    """(version lines, field lines, notes): paragraphs are separated by blank lines; the
    notes are everything after the fields, with their own blank lines."""
    lines = message.splitlines()
    for k, line in enumerate(lines):
        if SIGNATURE.match(line.strip()):
            del lines[k:]
            break
    paras: list[list[str]] = []
    i = 0
    while len(paras) < 2 and i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        paras.append(lines[i:j])
        i = j
    paras += [[]] * (2 - len(paras))
    return paras[0], paras[1], "\n".join(lines[i:]).strip("\n")


def parse(message):
    """The fields, read from the second paragraph only (the notes may say `key: ...`)."""
    fields = {}
    for line in split(message)[1]:
        m = re.match(r"([a-z][a-z0-9. -]*):\s*(.*)$", line)
        if m and m.group(1) in dict(FIELDS) and m.group(1) not in fields:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def verify(dist, linux, message, commit, allow_dirty=False):
    m, facts = load_dist(dist)
    lm, facts["image_sha256"] = load_linux(linux)
    for what, built in (("dist", m), ("kernel", lm)):
        if built["dirty"] and not allow_dirty:
            raise Mismatch(f"the {what} was built from a tree with uncommitted changes")
        if built["git_sha"] != commit:
            raise Mismatch(f"the {what} is of {built['git_sha']}, the tag points to {commit}")
    version = split(message)[0]
    if len(version) != 1 or not version[0].startswith("v"):
        raise Mismatch("tag message: the first paragraph must be the version alone, then a blank line")
    fields = parse(message)
    for key, _ in FIELDS:
        value = fields.get(key, "")
        if not value or FILL in value:
            raise Mismatch(f"tag message: '{key}' is missing or not filled in")
        if key == "commit" and not commit.startswith(value):
            raise Mismatch(f"tag message names commit {value}, the tag points to {commit}")
        if key.endswith("stage version") and value != facts[dict(FIELDS)[key]]:
            raise Mismatch(f"tag message: {key} {value}, the dist's is {facts[dict(FIELDS)[key]]}")
        if key == "pack.fs sha256" and value != facts["pack_sha256"]:
            raise Mismatch(
                f"pack.fs is {facts['pack_sha256']}, the tag message says {value}: "
                "this is not the bitstream that was tested"
            )
        if key == "linux image sha256" and value != facts["image_sha256"]:
            raise Mismatch(
                f"the Image is {facts['image_sha256']}, the tag message says {value}: "
                "this is not the kernel that was tested"
            )
    if not split(message)[2].strip():
        raise Mismatch("tag message: no release notes after the fields")
    if "<fill in" in message:
        raise Mismatch("tag message: something is not filled in yet")
    return m


def notes(message, commit):
    """Release notes: the fields as a code block, then the notes (Markdown) as they are."""
    _, fields, body = split(message)
    out = [f"Built by CI from {commit} and tested on a Tang Nano 9K:", "", "```", *fields, "```", ""]
    if body:
        out += [body, ""]
    out.append("Getting started: `docs/APP_DEVELOPMENT.md` in the archive. Tool versions: `MANIFEST.json`.")
    out.append("The noMMU Linux kernel: the `-kernel` archive; its `VUX9K/LINUX/` goes on the card.")
    return "\n".join(out) + "\n"


def fake_linux(tmp, git_sha, image=b"selftest Image"):
    """A make linux-dist tree for the selftest (make check-dist builds no kernel)."""
    os.makedirs(os.path.join(tmp, "VUX9K", "LINUX"))
    with open(os.path.join(tmp, LINUX_IMAGE), "wb") as f:
        f.write(image)
    with open(os.path.join(tmp, "MANIFEST.json"), "w") as f:
        json.dump({"git_sha": git_sha, "dirty": False}, f)
    with open(os.path.join(tmp, "SHA256SUMS"), "w") as f:
        f.write("".join(f"{sha256(os.path.join(tmp, rel))}  {rel}\n" for rel in (LINUX_IMAGE, "MANIFEST.json")))
    return tmp


def selftest(dist):
    with tempfile.TemporaryDirectory() as tmp:
        _selftest(dist, tmp)


def _selftest(dist, tmp):
    m, facts = load_dist(dist)
    linux = fake_linux(os.path.join(tmp, "linux"), m["git_sha"])
    image = load_linux(linux)[1]
    body = "## Notes\n\ntest-hw: a line in the notes is not a field\n\nsecond paragraph"
    filled = template(dist, linux, "v0.0.0-selftest").replace(NOTES_FILL, body).replace(FILL, "selftest")
    verify(dist, linux, filled, m["git_sha"], allow_dirty=True)
    published = notes(filled + "-----BEGIN PGP SIGNATURE-----\n\nxyz\n-----END PGP SIGNATURE-----\n", m["git_sha"])
    fence = published.split("```")
    if len(fence) != 3 or "compatibility: selftest" not in fence[1] or body not in fence[2] or "xyz" in published:
        raise Mismatch(f"selftest: notes didn't put the fields in a code block and the notes after it:\n{published}")
    bad = facts["pack_sha256"][:-1] + ("0" if facts["pack_sha256"][-1] != "0" else "1")
    bad_image = image[:-1] + ("0" if image[-1] != "0" else "1")
    for broken, why in (
        (filled.replace(facts["pack_sha256"], bad), "a different pack.fs hash"),
        (filled.replace(image, bad_image), "a different Image hash"),
        (filled.replace("test-hw-linux: selftest\n", ""), "a missing test-hw-linux"),
        (template(dist, linux), "unfilled results"),
        (template(dist, linux).replace(FILL, "selftest"), "unfilled release notes"),
        (filled.replace("\n\n" + body, ""), "no release notes"),
        (filled.replace("hw-smoke: selftest\n", ""), "a missing hw-smoke"),
        (
            filled.replace(f"first stage version: {facts['stage1_version']}\n", "first stage version: 0\n"),
            "a first stage version other than the dist's",
        ),
        (
            filled.replace(f"second stage version: {facts['stage2_version']}\n", "second stage version: 0\n"),
            "a second stage version other than the dist's",
        ),
        (filled.replace("compatibility: selftest", f"compatibility: {FILL}"), "an unfilled compatibility"),
        (filled.replace("test-hw: selftest\n", ""), "test-hw only in the notes"),
        (filled.replace("v0.0.0-selftest\n\n", "v0.0.0-selftest\n"), "no blank line after the version"),
    ):
        try:
            verify(dist, linux, broken, m["git_sha"], allow_dirty=True)
        except Mismatch:
            continue
        raise Mismatch(f"selftest: verify accepted {why}")
    # A kernel of another commit, and one whose Image changed after make linux-dist
    other = fake_linux(os.path.join(tmp, "other"), "0" * 40)
    changed = fake_linux(os.path.join(tmp, "changed"), m["git_sha"])
    with open(os.path.join(changed, LINUX_IMAGE), "ab") as f:
        f.write(b"!")
    for kernel, why in ((other, "a kernel of another commit"), (changed, "an Image that isn't SHA256SUMS'")):
        try:
            verify(dist, kernel, filled, m["git_sha"], allow_dirty=True)
        except Mismatch:
            continue
        raise Mismatch(f"selftest: verify accepted {why}")
    # A dist whose files changed after make_dist must fail too
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
    t.add_argument("--linux", required=True, help="make linux-dist's tree (CI's vux9k-linux-<sha>)")
    t.add_argument("--version", default="vX.Y.Z")
    v = sub.add_parser("verify")
    v.add_argument("dist")
    v.add_argument("message", help="file with the annotated tag's message")
    v.add_argument("--linux", required=True, help="make linux-dist's tree (CI's vux9k-linux-<sha>)")
    v.add_argument("--commit", required=True, help="full SHA the tag points to")
    n = sub.add_parser("notes")
    n.add_argument("message", help="file with the annotated tag's message")
    n.add_argument("--commit", required=True, help="full SHA the tag points to")
    s = sub.add_parser("selftest")
    s.add_argument("dist")
    args = p.parse_args()

    try:
        if args.cmd == "template":
            sys.stdout.write(template(args.dist, args.linux, args.version))
            sys.stderr.write(TEMPLATE_HELP)
        elif args.cmd == "verify":
            with open(args.message) as f:
                m = verify(args.dist, args.linux, f.read(), args.commit)
            print(f"release_check: OK ({m['git_sha']}, pack.fs and the Image as tested on the board)")
        elif args.cmd == "notes":
            with open(args.message) as f:
                sys.stdout.write(notes(f.read(), args.commit))
        else:
            selftest(args.dist)
            print("release_check: selftest OK")
    except Mismatch as e:
        sys.exit(f"release_check: FAIL: {e}")


if __name__ == "__main__":
    main()
