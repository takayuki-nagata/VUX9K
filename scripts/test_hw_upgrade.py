#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The upgrade from the previous releases on the board, its bitstream half, with the candidate's
second stage on the card (as test-hw-dist leaves it). First v0.3.x's bitstream (a CPU without
RV32M), whose first stage boots that second stage, which says it needs RV32M and stops: the
card written before the flash. Then the previous release's bitstream (the Makefile's
PREV_RELEASE), whose first stage boots the same second stage to `s2> `: a patch release's card
on the previous bitstream. Then the candidate's, whose first stage boots it to `s2> `. All are
SRAM loads (the flash is left alone) and none writes the card. The card half, cards as both
releases left them, is sim/emu/test_upgrade.py. The trees' SHA256SUMS are checked first. Ends
on the candidate's bitstream.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import release_check  # noqa: E402
from test_hardware import fail_msg, find_loader, load_bitstream, reopen_port, usb_hub_neighbours  # noqa: E402

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

# What the second stage prints on a CPU without M (firmware/stage2/bootstrap/start.s)
NO_M = "VUX9K Stage 2 needs a CPU with RV32M: program the v0.4.0 bitstream or later. Stopped."
OLD_TIMEOUT = 15.0


def manifest(dist):
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        return json.load(f)


def load(loader, dist, port, baud):
    """The dist's pack.fs into SRAM, then the board's UART opened again."""
    err = load_bitstream(loader, os.path.join(dist, "bitstream", "pack.fs"))
    if err:
        raise RuntimeError(f"bitstream not loaded: {err}")
    return reopen_port(port, baud)


def old_bitstream(ser, _versions):
    """v0.3.x's first stage boots the card's second stage on its own, and the second stage
    stops at its RV32M check. What was printed before the port was open is
    handed over at the open; the first stage's banner may be cut there, so only the second
    stage's message is required. Returns the text."""
    got, out = vux_tool.read_until(ser, (NO_M,), OLD_TIMEOUT)
    if not got:
        raise TimeoutError(fail_msg(f"No {NO_M!r}.", out))
    return out


def boots_the_second_stage(ser, versions):
    """The loaded bitstream's first stage (version s1), soft-reset from its prompt, boots the
    card's second stage (version s2) on its own: the candidate's, as test-hw-dist leaves it (a
    stale STAGE2.BIN fails). Returns the text."""
    s1_version, s2_version = versions
    vux_tool.enter_s1(ser, timeout=30.0)
    ser.write(b"r")
    ser.flush()
    got, out = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 30.0)
    want = f"VUX9K Stage 1 (v{s1_version})"
    want2 = f"VUX9K Stage 2 (v{s2_version})"
    if not got or want not in out or want2 not in out:
        raise TimeoutError(fail_msg(f"No {want!r} booting {want2!r} to {vux_tool.S2_PROMPT!r}.", out))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    p.add_argument("--old", required=True, metavar="DIR", help="the previous release's tree (make prev-release)")
    p.add_argument("--v03", required=True, metavar="DIR", help="v0.3.x's release tree (make prev-release)")
    p.add_argument("--new", required=True, metavar="DIR", help="the candidate's `make dist` tree")
    p.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    p.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    p.add_argument("--loader", default="openFPGALoader", help="openFPGALoader (default: on PATH)")
    args = p.parse_args()

    for dist in (args.v03, args.old, args.new):
        release_check.check_sums(dist)
    old, new = manifest(args.old), manifest(args.new)
    s2_version = new["stage2_version"]
    loader = find_loader(args.loader)
    if not loader:
        sys.exit("no openFPGALoader (--loader)")

    ser = vux_tool.open_port(args.port, baudrate=args.baud, timeout=0.1)
    neighbours = usb_hub_neighbours(ser.port)
    if neighbours:
        print(f"[WARN] USB devices share the board's hub ({', '.join(neighbours)}): the bridge may drop output")
    hint = vux_tool.modemmanager_hint(ser.port)
    ser.close()
    if hint:
        sys.exit(f"[FAIL] host setup: {hint}")

    ok = True
    for name, dist, check, version in (
        ("1. v0.3.x's bitstream: the second stage needs RV32M", args.v03, old_bitstream, None),
        (
            "2. The previous release's bitstream: its first stage boots the second stage",
            args.old,
            boots_the_second_stage,
            (old["stage1_version"], s2_version),
        ),
        (
            "3. The candidate's bitstream: its first stage boots the second stage",
            args.new,
            boots_the_second_stage,
            (new["stage1_version"], s2_version),
        ),
    ):
        try:
            ser = load(loader, dist, args.port, args.baud)
            try:
                check(ser, version)
            finally:
                ser.close()
            print(f"[PASS] {name}")
        except Exception as e:
            # The later steps run after a failed one too: the board ends on the candidate
            print(f"[FAIL] {name}: {e}")
            ok = False
    print("Upgrade on the board: " + ("PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
