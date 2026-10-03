#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Board smoke test of routed placements: `make hw-smoke`.

For each nextpnr seed routed under --seed-dir (seed_<N>/soc_pnr.json, as run_pnr.py
leaves them; `make timing` routes them all), put firmware/hw_test into the I-RAM block
RAMs' initial contents in place of the Boot Manager, pack, load it into the board's
SRAM and read hw_test's verdict from the UART. Only the INIT_RAM_* parameters change,
so the test runs on exactly that seed's placement and routing (the bitstream is
otherwise byte-identical to one built from scratch with hw_test as firmware.hex).

STA alone doesn't prove a bitstream works on the board (README, "Clock"); run this
before adopting a seed for `make prog-flash`.

I-RAM mapping (yosys' block-RAM mapping of soc_ram's i_mem): cells
`...ram_inst.i_mem.<copy>.<c>`, two identical copies (instruction and data port) of
eight DPB x4 cells; cell c holds bits [4c+3:4c] of word a as 4-bit entry a, and
INIT_RAM_LL holds entries 64*LL..64*LL+63 (entry e at bits [4e+3:4e]). Before patching,
the script checks that the routed netlist's contents match --base-hex (the firmware it
was synthesized with) under this mapping, so a changed mapping or a stale netlist
stops it instead of producing a wrong bitstream. D-RAM is left alone: the firmware
images start with an all-zero D-RAM (their .data is copied from I-RAM at startup),
which the script also checks.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

IMEM_WORDS = 4096
IMEM_CELL = re.compile(r"(^|\.)ram_inst\.i_mem\.([01])\.([0-7])$")
RESULT = re.compile(rb"RESULT (PASS|FAIL) ([0-9A-F]{2})/([0-9A-F]{2}) ([0-9A-F]{8})\n")


def read_hex(path):
    with open(path) as f:
        words = [int(w, 16) for w in f.read().split()]
    if len(words) > IMEM_WORDS:
        sys.exit(f"{path}: {len(words)} words, I-RAM holds {IMEM_WORDS}")
    return words + [0] * (IMEM_WORDS - len(words))


def init_lines(words, col):
    """INIT_RAM_00..3F of the DPB x4 cell holding nibble `col` of each word."""
    lines = {}
    for line in range(64):
        v = 0
        for e in range(64):
            v |= ((words[line * 64 + e] >> (4 * col)) & 0xF) << (4 * e)
        lines[f"INIT_RAM_{line:02X}"] = f"{v:0256b}"
    return lines


def imem_cells(netlist):
    cells = netlist["modules"][next(iter(netlist["modules"]))]["cells"]
    found = {}
    for name, cell in cells.items():
        m = IMEM_CELL.search(name)
        if m:
            found[name] = (cell, int(m.group(3)))
    if len(found) != 16:
        sys.exit(f"expected 16 I-RAM block-RAM cells (ram_inst.i_mem.<0-1>.<0-7>), found {len(found)}")
    return found


def patch(routed_path, base_words, new_words, out_path):
    with open(routed_path) as f:
        netlist = json.load(f)
    for name, (cell, col) in imem_cells(netlist).items():
        params = cell["parameters"]
        expected = init_lines(base_words, col)
        if any(params.get(k) != v for k, v in expected.items()):
            sys.exit(
                f"{routed_path}: {name} doesn't hold --base-hex under the known mapping "
                "(stale netlist, or yosys mapped soc_ram differently)"
            )
        params.update(init_lines(new_words, col))
    with open(out_path, "w") as f:
        json.dump(netlist, f)


def check_dram_zero(fw_dir):
    for i in range(4):
        path = os.path.join(fw_dir, f"firmware_d{i}.hex")
        if os.path.exists(path):
            with open(path) as f:
                if any(int(w, 16) for w in f.read().split()):
                    sys.exit(f"{path} is not all zero; this script only rewrites I-RAM")


def run_on_board(fs, port, timeout):
    import tools.vux_tool as vux_tool

    ser = vux_tool.open_port(port, baudrate=115200, timeout=0.05)
    try:
        ser.reset_input_buffer()
        subprocess.run(["openFPGALoader", "-b", "tangnano9k", fs], check=True, capture_output=True)
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            buf += ser.read(4096)
            if RESULT.search(buf):
                break
        return buf
    finally:
        ser.close()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--hex", required=True, help="hw_test image (4096-word I-RAM hex)")
    p.add_argument("--base-hex", required=True, help="firmware.hex the netlists were synthesized with")
    p.add_argument("--seed-dir", required=True, help="directory of seed_<N>/soc_pnr.json")
    p.add_argument("--out", required=True, help="where the patched bitstreams go")
    p.add_argument("--seeds", nargs="*", type=int, help="default: every routed seed")
    p.add_argument("--port", default="auto")
    # hw_test repeats its RESULT line every 2 s, in case the bridge drops the first one
    p.add_argument("--timeout", type=float, default=20.0, help="seconds per seed")
    args = p.parse_args()

    check_dram_zero(os.path.dirname(args.hex))
    base, new = read_hex(args.base_hex), read_hex(args.hex)
    seeds = args.seeds or sorted(
        int(d.split("_")[1])
        for d in os.listdir(args.seed_dir)
        if d.startswith("seed_") and os.path.exists(os.path.join(args.seed_dir, d, "soc_pnr.json"))
    )
    if not seeds:
        sys.exit(f"no routed seeds under {args.seed_dir} (run `make timing`)")
    os.makedirs(args.out, exist_ok=True)

    results = {}
    for seed in seeds:
        routed = os.path.join(args.seed_dir, f"seed_{seed}", "soc_pnr.json")
        js = os.path.join(args.out, f"seed_{seed}.json")
        fs = os.path.join(args.out, f"seed_{seed}.fs")
        patch(routed, base, new, js)
        subprocess.run(["gowin_pack", "-d", "GW1N-9C", "-o", fs, js], check=True, capture_output=True)
        out = run_on_board(fs, args.port, args.timeout)
        with open(os.path.join(args.out, f"seed_{seed}.log"), "wb") as f:
            f.write(out)
        m = RESULT.search(out)
        if m:
            verdict = f"{m.group(1).decode()} {int(m.group(2), 16)}/{int(m.group(3), 16)}"
            mask = int(m.group(4), 16)
            failed = [f"T{n + 1:02X}" for n in range(32) if mask >> n & 1]
        else:
            verdict, failed = f"NO RESULT ({len(out)} bytes)", []
        results[seed] = (m is not None and m.group(1) == b"PASS", verdict, failed)
        print(f"seed {seed:>4}: {verdict}{'  failed: ' + ' '.join(failed) if failed else ''}", flush=True)

    ok = all(r[0] for r in results.values())
    print(
        f"=== hw-smoke: {sum(r[0] for r in results.values())}/{len(results)} seeds pass"
        f" (logs and bitstreams in {args.out}) ==="
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
