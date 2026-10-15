#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The Zephyr interrupt-driven UART echo (make build-zephyr-irq-echo) on the board: put on the
card as \\VUX9K\\APPS\\IRQECHO.BIN through the second stage (`put`, the file left there) and
started with its `run`, then sent lines longer than both 32-byte UART FIFOs; each must come
back upper-cased with "rx=<n> drop=0 err=0" (no byte lost in the app or by the UART). From
the uncached PSRAM the app can't read a line sent back to back at 115200 baud, so each is
sent in paced chunks (sim/integration/irq_echo_pace.py, measured on the emulator; --chunk 0
sends it back to back, which should pass again with the cache, B4a). The
application runs in the PSRAM, its handlers too, and never returns: the first stage stays in
I-RAM, but only the reset button would start it again, so the bitstream is loaded into the
FPGA's SRAM from --pack-fs at the end (not openFPGALoader --reset, which loads the flash's
bitstream). With --dist both come from a `make dist` tree, so the board is
left on that tree's bitstream rather than the working tree's. Needs the board at a prompt of
either stage with a second stage on the card (as make test-hw leaves it); the card stays in
the board.
"""

import argparse
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "integration"))

import irq_echo_pace as pace  # noqa: E402
from test_hardware import (  # noqa: E402
    CARD_APPS,
    DIST_APPS_DIR,
    DIST_FILES,
    find_loader,
    load_bitstream,
    mkdir_p,
    usb_hub_neighbours,
)

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

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


def v4_file(data: bytes) -> bytes:
    """data as the card's v4 file: IRQECHO.BIN as it is, a raw zephyr.bin wrapped (RV32 at
    the PSRAM's start, where the board vux9k links it)."""
    if vux_tool.ImageHeader.parse(data):
        return data
    return vux_tool.build_vux9_v4_image(data, name="Zephyr irq_echo", mode="riscv", load_addr=vux_tool.XMEM_BASE)[0]


def send_chunked(ser, data: bytes, chunk: int) -> None:
    """data in chunks of `chunk` bytes, each followed by pace.GAP_FRAMES frame times of
    silence (0: all at once). The sleep counts from the write, so it covers the chunk's own
    time on the line too; host and USB latency only add to the gaps."""
    if chunk <= 0:
        ser.write(data)
        ser.flush()
        return
    frame_s = pace.FRAME_BITS / 115200
    for piece in pace.chunks(data, chunk):
        ser.write(piece)
        ser.flush()
        time.sleep((len(piece) + pace.GAP_FRAMES) * frame_s)


def run(ser, image: bytes, size: int, count: int, chunk: int) -> bool:
    try:
        vux_tool.enter_s2(ser)
    except TimeoutError as e:
        hint = vux_tool.garbled_hint(str(e))
        print(f"[FAIL] no second-stage prompt: {e}" + (f"\n       -> {hint}" if hint else ""))
        return False
    mkdir_p(ser, CARD_APPS)
    print(vux_tool.put(ser, f"{CARD_APPS}/IRQECHO.BIN", image, timeout=20.0))
    print(vux_tool.run_app(ser, f"{CARD_APPS}/IRQECHO.BIN").strip().splitlines()[-1])
    read_until(ser, b"irq_echo ready\n", timeout=10.0)
    time.sleep(0.2)

    passed = True
    for k in range(count):
        data = burst(size, k)
        send_chunked(ser, data + b"\n", chunk)
        out = read_until(ser, b"err=", timeout=5.0)
        out += read_until(ser, b"\n", timeout=1.0) if not out.endswith(b"\n") else b""
        expected = data.upper() + f"\nrx={size} drop=0 err=0\n".encode()
        good = out == expected
        passed &= good
        how = f"in {chunk}-byte chunks" if chunk > 0 else "back to back"
        print(f"[{'PASS' if good else 'FAIL'}] line {k + 1}/{count}: {size} bytes {how}")
        if not good:
            print(f"       expected {expected!r}\n       got      {out!r}")
    return passed


def resolve_files(args):
    """(app, pack.fs): --bin and --pack-fs, else from --dist, else the working tree's build."""
    if args.dist:
        app = args.bin or os.path.join(args.dist, DIST_APPS_DIR, "IRQECHO.BIN")
        return app, args.pack_fs or os.path.join(args.dist, DIST_FILES["pack_fs"])
    if not args.bin:
        sys.exit("hw_irq_echo: --bin or --dist is needed")
    return args.bin, args.pack_fs or os.path.join(REPO_ROOT, "build", "synth", "pack.fs")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--bin", help="the echo app: IRQECHO.BIN (make apps) or its zephyr.bin")
    parser.add_argument("--dist", metavar="DIR", help="a `make dist` tree: its IRQECHO.BIN and pack.fs")
    parser.add_argument(
        "--pack-fs",
        help="bitstream to load into SRAM afterwards, back to the first stage (default: build/synth/pack.fs)",
    )
    parser.add_argument("--loader", default="openFPGALoader", help="openFPGALoader to load it with")
    parser.add_argument("--port", default="auto", help="serial port (default: auto)")
    parser.add_argument("--size", type=int, default=256, help="bytes per line (default: 256)")
    parser.add_argument("--count", type=int, default=5, help="lines (default: 5)")
    parser.add_argument(
        "--chunk",
        type=int,
        default=pace.CHUNK,
        help=f"send each line in chunks of this many bytes with gaps, 0: back to back (default: {pace.CHUNK})",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    app, pack_fs = resolve_files(args)
    with open(app, "rb") as f:
        image = v4_file(f.read())
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
        passed = run(ser, image, args.size, args.count, args.chunk)
    except (TimeoutError, vux_tool.UploadError, vux_tool.S2Error) as e:
        print(f"[FAIL] {e}")
        passed = False
    finally:
        ser.close()
        if str(args.port).startswith("socket://"):
            print("[INFO] the emulator: no bitstream to load")
        else:
            err = load_bitstream(find_loader(args.loader), pack_fs)
            if err:
                print(f"[WARN] {err}: the board stays in the echo app until the bitstream is loaded")
    print("IRQ echo on the board: " + ("PASSED" if passed else "FAILED"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
