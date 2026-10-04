#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Line coverage of the host Rust crates: per-crate totals from cargo-llvm-cov, checked against minimums.

`make coverage-rust` exports `cargo llvm-cov report --json --summary-only` for the emulator
workspace (cargo test plus the Python-driven runs of the instrumented module and CLI) and
for fw_common's host tests. This script splits the files of those reports into crates by
path, prints each crate's line coverage, writes a Markdown summary with every file, and
fails when a crate is below its minimum in the thresholds file's [rust] table, has no
minimum there, or a listed crate has no data.
"""

import argparse
import json
import os
import sys
import tomllib

# crate -> the directory (relative to the repository) its sources live in
CRATES = {
    "vux9k_emu": "emu/crates/vux9k_emu/",
    "vux9k_emu_cli": "emu/crates/vux9k_emu_cli/",
    "vux9k_emu_py": "emu/crates/vux9k_emu_py/",
    "fw_common": "firmware/fw_common/",
}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(paths):
    """{crate: [(relative path, covered lines, lines)]} over all reports."""
    crates: dict[str, list[tuple[str, int, int]]] = {}
    for path in paths:
        with open(path) as f:
            report = json.load(f)
        for export in report["data"]:
            for entry in export["files"]:
                rel = os.path.relpath(entry["filename"], REPO_ROOT)
                crate = next((c for c, d in CRATES.items() if rel.startswith(d)), None)
                if crate is None:
                    sys.exit(f"{path}: {rel} belongs to no crate in CRATES")
                lines = entry["summary"]["lines"]
                crates.setdefault(crate, []).append((rel, lines["covered"], lines["count"]))
    return crates


def pct(covered, count):
    return 100.0 * covered / count if count else 100.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("reports", nargs="+", help="cargo llvm-cov report --json --summary-only outputs")
    ap.add_argument("--thresholds", required=True, help="TOML: [rust] crate = minimum percent")
    ap.add_argument("--summary", required=True, help="Markdown output")
    args = ap.parse_args()

    crates = load(args.reports)
    with open(args.thresholds, "rb") as f:
        thresholds = tomllib.load(f).get("rust", {})

    md = [
        "# Line coverage of the host Rust crates",
        "",
        "| crate | file | lines | covered | % |",
        "|---|---|---|---|---|",
    ]
    failed = []
    for crate in sorted(crates):
        files = sorted(crates[crate])
        for rel, covered, count in files:
            md.append(f"| {crate} | {rel} | {count} | {covered} | {pct(covered, count):.1f} |")
        covered = sum(f[1] for f in files)
        count = sum(f[2] for f in files)
        total = pct(covered, count)
        md.append(f"| **{crate}** | | **{count}** | **{covered}** | **{total:.1f}** |")
        want = thresholds.get(crate)
        limit = f"  (min {want}%)" if want is not None else "  (no minimum)"
        print(f"{crate:14} {total:5.1f}% ({covered}/{count}){limit}")
        if want is None:
            failed.append(f"{crate}: no minimum in [rust]")
        elif total + 1e-9 < want:
            failed.append(f"{crate}: {total:.1f}% < {want}%")
    failed += [f"{c}: no data (did its tests run?)" for c in sorted(set(thresholds) - set(crates))]

    os.makedirs(os.path.dirname(os.path.abspath(args.summary)), exist_ok=True)
    with open(args.summary, "w") as out:
        out.write("\n".join(md) + "\n")
    if failed:
        sys.exit("Rust coverage: " + "; ".join(failed))


if __name__ == "__main__":
    main()
