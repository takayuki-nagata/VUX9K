#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The upgrade from the previous release on the board, its bitstream half: the previous
release's bitstream (v0.2.x, the Boot Manager) loaded into the FPGA's SRAM, its Boot Manager
seen at its `vux>` prompt; then the candidate's, whose first stage boots the second stage on
the card to `s2> `. Both are SRAM loads (the flash is left alone) and neither writes the card:
the Boot Manager only reads slot 0's header as it starts (a v0.2.x layout, slots in LBA
64-703, isn't needed; the card stays FAT32 as test-hw-dist left it). The card half, a v0.2.x
card through docs/APP_DEVELOPMENT.md's steps, is sim/emu/test_upgrade.py. Both trees'
SHA256SUMS are checked first. Ends on the candidate's bitstream.
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

BM_PROMPT = "vux> "
# The Boot Manager's banner as it starts
BM_BANNER = "VUX9K Dual-ISA RISC-V / Hack SoC Boot Manager (v{})"
BM_TIMEOUT = 15.0


def manifest(dist):
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        return json.load(f)


def load(loader, dist, port, baud):
    """The dist's pack.fs into SRAM, then the board's UART opened again."""
    err = load_bitstream(loader, os.path.join(dist, "bitstream", "pack.fs"))
    if err:
        raise RuntimeError(f"bitstream not loaded: {err}")
    return reopen_port(port, baud)


def old_boot_manager(ser, version):
    """The previous release's Boot Manager at its prompt: its start banner (printed before the
    port was open, handed over at the open) names its version, `h` its prompt. Not `r`: that
    reboots through the Resident Loader from slot 0, which a FAT32 card from mkfs doesn't have
    (it stops at "[RL] Slot 0"). Returns the text."""
    ser.write(b"h")
    ser.flush()
    got, out = vux_tool.read_until(ser, (BM_PROMPT,), BM_TIMEOUT)
    want = BM_BANNER.format(version)
    if not got or want not in out:
        raise TimeoutError(fail_msg(f"No {want!r} and {BM_PROMPT!r} after h.", out))
    return out


def new_stages(ser, versions):
    """The candidate's first stage, soft-reset from its prompt, boots the card's second stage
    on its own: the candidate's, as test-hw-dist leaves it (a stale STAGE2.BIN fails).
    Returns the text."""
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
    p.add_argument("--new", required=True, metavar="DIR", help="the candidate's `make dist` tree")
    p.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    p.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    p.add_argument("--loader", default="openFPGALoader", help="openFPGALoader (default: on PATH)")
    args = p.parse_args()

    for dist in (args.old, args.new):
        release_check.check_sums(dist)
    bm_version = manifest(args.old)["boot_manager_version"]
    new = manifest(args.new)
    versions = (new["stage1_version"], new["stage2_version"])
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
        ("1. The previous release's bitstream: its Boot Manager", args.old, old_boot_manager, bm_version),
        ("2. The candidate's bitstream: its first stage boots the second stage", args.new, new_stages, versions),
    ):
        try:
            ser = load(loader, dist, args.port, args.baud)
            try:
                check(ser, version)
            finally:
                ser.close()
            print(f"[PASS] {name}")
        except Exception as e:
            # Step 2 runs after a failed step 1 too: the board ends on the candidate
            print(f"[FAIL] {name}: {e}")
            ok = False
    print("Upgrade on the board: " + ("PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
