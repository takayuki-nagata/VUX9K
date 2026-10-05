#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (make build-zephyr-irq-echo) on the board:
flashes it to SD slot 3 through the running Boot Manager, boots it, and sends bursts
longer than both 32-byte UART FIFOs; each must come back upper-cased with
"rx=<n> drop=0 err=0" (no byte lost in the app or by the UART). The application never
returns to the Boot Manager, so the FPGA is reconfigured from --pack-fs at the end.
Slot 3 is otherwise only used by test_hardware.py's test 12, which overwrites it.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from test_hardware import usb_hub_neighbours  # noqa: E402

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

SLOT = 3
ALPHABET = b"abcdefghijklmnopqrstuvwxyz0123456789"


def read_until(ser, token: bytes, timeout: float) -> bytes:
    """Bytes read until token arrives; raises TimeoutError with the output otherwise."""
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out += ser.read(256)
        if token in out:
            return out
    raise TimeoutError(f"{token!r} not received within {timeout} s; got {out!r}")


def burst(n: int, k: int) -> bytes:
    """n bytes, a different rotation of ALPHABET for each burst k."""
    return bytes(ALPHABET[(i + 7 * k) % len(ALPHABET)] for i in range(n))


def run(ser, image: bytes, size: int, count: int) -> bool:
    ok, resp = vux_tool.sync_prompt(ser, timeout=4.0)
    if not ok:
        hint = vux_tool.garbled_hint(resp)
        print(f"[FAIL] no Boot Manager prompt: {resp!r}" + (f"\n       -> {hint}" if hint else ""))
        return False
    vux_tool.flash_slot(ser, image, slot=SLOT, name="IRQ echo", mode="riscv")
    vux_tool.sync_prompt(ser, timeout=4.0)
    vux_tool.drain_serial(ser, timeout=0.1)
    ser.write(str(SLOT).encode())
    ser.flush()
    read_until(ser, b"irq_echo ready\n", timeout=10.0)
    time.sleep(0.2)

    passed = True
    for k in range(count):
        data = burst(size, k)
        ser.write(data + b"\n")
        ser.flush()
        out = read_until(ser, b"err=", timeout=5.0)
        out += read_until(ser, b"\n", timeout=1.0) if not out.endswith(b"\n") else b""
        expected = data.upper() + f"\nrx={size} drop=0 err=0\n".encode()
        good = out == expected
        passed &= good
        print(f"[{'PASS' if good else 'FAIL'}] burst {k + 1}/{count}: {size} bytes")
        if not good:
            print(f"       expected {expected!r}\n       got      {out!r}")
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--bin", required=True, help="the echo app's zephyr.bin")
    parser.add_argument(
        "--pack-fs",
        default=os.path.join(REPO_ROOT, "build", "synth", "pack.fs"),
        help="bitstream to reconfigure the FPGA with afterwards (back to the Boot Manager)",
    )
    parser.add_argument("--loader", default="openFPGALoader", help="openFPGALoader to reconfigure with")
    parser.add_argument("--port", default="auto", help="serial port (default: auto)")
    parser.add_argument("--size", type=int, default=256, help="bytes per burst (default: 256)")
    parser.add_argument("--count", type=int, default=5, help="bursts (default: 5)")
    args = parser.parse_args()

    with open(args.bin, "rb") as f:
        image = f.read()
    ser = vux_tool.open_port(args.port, baudrate=115200, timeout=0.1)
    neighbours = usb_hub_neighbours(ser.port)
    if neighbours:
        print(f"[WARN] USB devices share the board's hub ({', '.join(neighbours)}); output can be lost")
    mm_hint = vux_tool.modemmanager_hint(ser.port)
    if mm_hint:
        ser.close()
        print(f"[FAIL] {mm_hint}")
        sys.exit(1)
    try:
        passed = run(ser, image, args.size, args.count)
    except TimeoutError as e:
        print(f"[FAIL] {e}")
        passed = False
    finally:
        ser.close()
        loader = shutil.which(args.loader) or os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")
        reconfigured = False
        if os.path.isfile(args.pack_fs):
            try:
                cmd = [loader, "-b", "tangnano9k", args.pack_fs]
                reconfigured = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            except OSError:
                pass
        if not reconfigured:
            print(f"[WARN] could not reconfigure from {args.pack_fs}: the board stays in the echo app")
    print("IRQ echo on the board: " + ("PASSED" if passed else "FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
