#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
One-line timing/area summary of the current synthesis + PnR run (make timing).

Reads build/synth/soc.json (cell counts), build/synth/pnr/seed_<N>/soc_sta.json for
each seed of --seeds (worst slack per seed) and the adopted seed's routed netlist
(start/end flops of the worst path), prints a small report and appends one TSV row
to --history, so every RTL commit of the timing work leaves a comparable record.

Register names do not survive synthesis (synth_gowin ends with `autoname`, which
renames nets after abc), so the worst path's end points are identified by their
flops' `src` attribute (generated .sv file and line range of the always_ff block)
and by the leading part of the net name autoname derived for their D input.
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime

CELL_GROUPS = {
    "LUT": ("LUT1", "LUT2", "LUT3", "LUT4"),
    "MUX2": ("MUX2_LUT5", "MUX2_LUT6", "MUX2_LUT7", "MUX2_LUT8"),
    "FF": ("DFF", "DFFE", "DFFC", "DFFCE", "DFFP", "DFFPE", "DFFS", "DFFSE", "DFFR", "DFFRE", "DFFN", "DFFNE"),
    "ALU": ("ALU",),
    "BSRAM": ("DPB", "DPX9B", "SP", "SPX9", "SDPB", "SDPX9B"),
    "SSRAM": ("RAM16SDP4", "RAM16SDP2", "RAM16SDP1", "RAM16S4"),
}
# autoname appends _LUT3_F_..., _DFFCE_Q_... etc. to the name of the net it started from
AUTONAME_SUFFIX = re.compile(r"_(LUT\d|MUX2|DFF\w*|ALU|RAM16\w*|DO|DI|DPB|SPX9)(_|\[|$).*")


def cell_counts(soc_json):
    top = json.load(open(soc_json))["modules"]["soc_top"]
    types = {}
    for cell in top["cells"].values():
        types[cell["type"]] = types.get(cell["type"], 0) + 1
    return {group: sum(types.get(t, 0) for t in members) for group, members in CELL_GROUPS.items()}


def worst_slack(sta_json, freq):
    paths = json.load(open(sta_json)).get("critical_paths", [])
    period = 1000.0 / freq
    return min(period - sum(e.get("delay", 0.0) for e in p.get("path", [])) for p in paths) if paths else 0.0


def endpoint_desc(cell, port, netnames_by_bit):
    src = [s for s in cell.get("attributes", {}).get("src", "").split("|") if "build/veryl/" in s]
    where = src[0].split("build/veryl/")[-1] if src else "?"
    bit = cell["connections"].get(port, [None])[0]
    nets = sorted(netnames_by_bit.get(bit, []), key=len)
    net = AUTONAME_SUFFIX.sub("", nets[0]) if nets else "?"
    return f"{where} ({port}={net})"


def worst_path_endpoints(pnr_json, sta_json):
    modules = json.load(open(pnr_json))["modules"]
    top = modules[next(iter(modules))]
    by_bit = {}
    for name, net in top["netnames"].items():
        for bit in net["bits"]:
            by_bit.setdefault(bit, []).append(name)
    path = json.load(open(sta_json))["critical_paths"][0]["path"]
    cells = top["cells"]
    start = cells.get(path[0]["from"]["cell"])
    end = cells.get(path[-1]["to"]["cell"])
    return (
        endpoint_desc(start, "D", by_bit) if start else "?",
        endpoint_desc(end, "D", by_bit) if end else "?",
    )


def git_rev():
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True)
        return rev + ("+dirty" if dirty.stdout.strip() else "")
    except FileNotFoundError:
        return "?"


def main():
    parser = argparse.ArgumentParser(description="Summarize area and per-seed timing of the last PnR run.")
    parser.add_argument("--synth-dir", default="build/synth")
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--freq", type=float, default=30.0)
    parser.add_argument("--history", help="TSV file to append one row to")
    args = parser.parse_args()

    soc_json = os.path.join(args.synth_dir, "soc.json")
    counts = cell_counts(soc_json)
    slacks = {}
    for seed in args.seeds:
        sta = os.path.join(args.synth_dir, "pnr", f"seed_{seed}", "soc_sta.json")
        if not os.path.exists(sta):
            print(f"timing_summary.py: seed {seed}: no report (nextpnr failed or was stopped)", file=sys.stderr)
        elif os.path.getmtime(sta) < os.path.getmtime(soc_json):
            print(f"timing_summary.py: seed {seed}: report predates soc.json, ignored", file=sys.stderr)
        else:
            slacks[seed] = worst_slack(sta, args.freq)
    if not slacks:
        print("timing_summary.py: no per-seed timing reports found", file=sys.stderr)
        return 1
    best_seed = max(slacks, key=lambda s: (slacks[s], -args.seeds.index(s)))
    best, median = slacks[best_seed], statistics.median(slacks.values())
    period = 1000.0 / args.freq
    start, end = worst_path_endpoints(
        os.path.join(args.synth_dir, "pnr", f"seed_{best_seed}", "soc_pnr.json"),
        os.path.join(args.synth_dir, "pnr", f"seed_{best_seed}", "soc_sta.json"),
    )
    rev = git_rev()

    print("=" * 80)
    print(f"Timing summary @ {args.freq:.1f} MHz, {rev}")
    print("  cells  : " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("  slack  : " + ", ".join(f"seed {s}: {v:+.2f}" for s, v in sorted(slacks.items())))
    print(
        f"  best   : {best:+.2f} ns (seed {best_seed}, Fmax {1000.0 / (period - best):.2f} MHz), "
        f"median {median:+.2f} ns (Fmax {1000.0 / (period - median):.2f} MHz)"
    )
    print(f"  worst path (seed {best_seed}): from {start}")
    print(f"                        to   {end}")
    print("=" * 80)

    if args.history:
        os.makedirs(os.path.dirname(args.history) or ".", exist_ok=True)
        header = ["date", "rev", *counts, "best_ns", "best_seed", "median_ns", "slacks", "worst_from", "worst_to"]
        new = not os.path.exists(args.history)
        with open(args.history, "a", encoding="utf-8") as f:
            if new:
                f.write("\t".join(header) + "\n")
            row = [
                datetime.now().isoformat(timespec="minutes"),
                rev,
                *(str(v) for v in counts.values()),
                f"{best:+.2f}",
                str(best_seed),
                f"{median:+.2f}",
                " ".join(f"{s}:{v:+.2f}" for s, v in sorted(slacks.items())),
                start,
                end,
            ]
            f.write("\t".join(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
