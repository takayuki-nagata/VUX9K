#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Functional coverage of the unit tests: merge their fcov.yml files and check the minimums.

Each unit test module that samples cover points (sim/unit/fcov.py) writes fcov.yml into its
run directory. Cover points are named "<group>.<point>"; a group belongs to one test module.
This script reads the files, prints each group's coverage and the bins nothing hit, writes
a Markdown summary, and fails when a group is below its minimum in the thresholds file's
[fcov] table, has no minimum there, or a listed group has no data (its test didn't run).
"""

import argparse
import os
import sys
import tomllib

import yaml


def load(paths):
    """{group: {name: item}} over all files; a group may come from one file only."""
    groups: dict[str, dict[str, dict]] = {}
    origin: dict[str, str] = {}
    for path in paths:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for name, item in data.items():
            group = name.split(".")[0]
            if origin.setdefault(group, path) != path:
                sys.exit(f"cover group {group!r} in both {origin[group]} and {path}")
            groups.setdefault(group, {})[name] = item
    return groups


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="fcov.yml files")
    ap.add_argument("--thresholds", required=True, help="TOML: [fcov] group = minimum percent")
    ap.add_argument("--summary", required=True, help="Markdown output")
    args = ap.parse_args()

    groups = load(args.files)
    with open(args.thresholds, "rb") as f:
        thresholds = tomllib.load(f).get("fcov", {})

    md = ["# Functional coverage of the unit tests", "", "| group | point | % | bins not hit |", "|---|---|---|---|"]
    failed = []
    for group in sorted(groups):
        items = groups[group]
        pct = items[group]["cover_percentage"] if group in items else 0.0
        for name in sorted(n for n in items if n != group):
            item = items[name]
            missed = [str(b) for b, hits in (item.get("bins:_hits") or {}).items() if hits < item.get("at_least", 1)]
            md.append(f"| {group} | {name} | {item['cover_percentage']:.1f} | {', '.join(missed)} |")
            if missed:
                print(f"  {name}: not hit: {', '.join(missed)}")
        want = thresholds.get(group)
        md.append(f"| **{group}** | | **{pct:.1f}** | |")
        print(f"{group:12} {pct:5.1f}%" + (f"  (min {want}%)" if want is not None else "  (no minimum)"))
        if want is None:
            failed.append(f"{group}: no minimum in [fcov]")
        elif pct + 1e-9 < want:
            failed.append(f"{group}: {pct:.1f}% < {want}%")
    failed += [f"{g}: no data (did its test run?)" for g in sorted(set(thresholds) - set(groups))]

    os.makedirs(os.path.dirname(os.path.abspath(args.summary)), exist_ok=True)
    with open(args.summary, "w") as f:
        f.write("\n".join(md) + "\n")
    if failed:
        sys.exit("functional coverage: " + "; ".join(failed))


if __name__ == "__main__":
    main()
