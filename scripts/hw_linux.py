#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The noMMU Linux kernel (make linux-dist, or CI's vux9k-linux-<sha>) booted on the board to
busybox's shell, timed from the bitstream's load: its Image and vux9k.dtb put on the card as
\\VUX9K\\LINUX\\Image and vux9k.dtb through the second stage (`put`, the files left there;
--keep-card uses those already there), then pack.fs loaded into the FPGA's SRAM, the first
stage booting the card's second stage by itself, whose `linux` loads both files and starts
the kernel. The console must show sim/integration/linux_checks.py's boot lines and answer its
commands, as on the emulator (sim/emu/test_linux.py); right after the boot the shell's builtins
read /proc/meminfo and /proc/buddyinfo, and [MEM] lines give linux_checks' memory table.
Linux never returns to the first stage, so pack.fs is loaded once more at the end (not
openFPGALoader --reset, which loads the flash's bitstream). [TIME] lines give the wall-clock
seconds: the put, the load to the second stage's prompt, `linux` to its [LINUX] line (reading the card), [LINUX] to the
shell's prompt (the kernel's boot), the bitstream's load to that prompt, and each command.
With --dist the bitstream comes from a `make dist` tree. Against the emulator
(--port socket://...) there is no bitstream to load: the times start when the port opens.
Needs a card with the second stage on it (as make test-hw leaves it); the card stays in the
board.
"""

import argparse
import json
import os
import re
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "integration"))

import linux_checks  # noqa: E402
from test_hardware import (  # noqa: E402
    DIST_FILES,
    find_loader,
    load_bitstream,
    mkdir_p,
    reopen_port,
    usb_hub_neighbours,
)

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

CARD_LINUX = "VUX9K/LINUX"
# make linux-dist's tree: its card files (as they go on the card) and its manifest
CARD_FILES = ("VUX9K/LINUX/Image", "VUX9K/LINUX/vux9k.dtb")
# Wall-clock bounds, not measurements: on the emulator the Image's load took ~13 s and the
# boot ~12.5 s at 18 MHz, a command ~1 s (docs/agents/linux.md, "Speed"); a put of the
# 3.2 MB Image ~6 min at ~9 KB/s
S2_TIMEOUT = 30.0
# The first stage boots the second ~0.7 s after the load (the board, 2026-11-11)
S2_PROMPT_WAIT = 5.0
LOAD_TIMEOUT = 120.0
BOOT_TIMEOUT = 180.0
COMMAND_TIMEOUT = 30.0
PUT_LINE_TIMEOUT = 30.0


def read_until(ser, token: str, timeout: float, text: str = "") -> str:
    """The console's text up to and including token; raises TimeoutError with what came."""
    got, text = vux_tool.read_until(ser, (token,), timeout, text)
    if not got:
        raise TimeoutError(f"{token!r} not received within {timeout} s; got {text[-600:]!r}")
    return text


def send_line(ser, line: str) -> None:
    """A command line in chunks with gaps: Linux's liteuart driver polls the 32-byte RX FIFO
    on the timer tick (HZ 100), so a long line sent back to back could overrun it."""
    data = (line + "\n").encode()
    for i in range(0, len(data), 8):
        ser.write(data[i : i + 8])
        ser.flush()
        time.sleep(0.02)


def put_files(ser, files: dict[str, bytes], times: dict[str, float]) -> None:
    """At the second stage's prompt: the kernel's files onto the card in \\VUX9K\\LINUX\\."""
    mkdir_p(ser, "VUX9K")
    mkdir_p(ser, CARD_LINUX)
    for path, data in files.items():
        t = time.monotonic()
        print(vux_tool.put(ser, path, data, timeout=PUT_LINE_TIMEOUT))
        times[f"put {os.path.basename(path)} ({len(data)} B)"] = time.monotonic() - t


def banner(image: bytes) -> str | None:
    """The kernel's `Linux version ...` line as the Image holds it: its build number and
    time tell this build from another of the same size, so the console must print it."""
    m = re.search(rb"Linux version [ -~]*#\d+ [ -~]*", image)
    return m.group().decode() if m else None


def run_command(ser, cmd: str) -> str | None:
    """One command typed at the shell: what follows its echo up to the next prompt, None
    without the echo."""
    send_line(ser, cmd)
    # The echo, the reply and the next prompt can come in one read: read to the prompt
    out = read_until(ser, linux_checks.PROMPT, COMMAND_TIMEOUT)
    at = out.find(cmd)
    return None if at < 0 else out[at + len(cmd) :]


def boot(
    ser, t0: float, image: bytes, dtb: bytes, times: dict[str, float], sizes: dict[str, int], mem: dict[str, str]
) -> list[str]:
    """From the first stage's own boot of the second stage (started at t0) to the shell's
    prompt, then the memory table (into mem) and the commands; returns the failures."""
    failures = []
    got, text = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), S2_PROMPT_WAIT)
    if not got:
        # The prompt went by before the port was open, or the first stage stayed at its own:
        # the time to the prompt then includes this wait
        print(f"[INFO] no second-stage prompt within {S2_PROMPT_WAIT} s of the load: typing for it")
        text += vux_tool.enter_s2(ser, timeout=S2_TIMEOUT)
    times["load to the second stage's prompt"] = time.monotonic() - t0
    t = time.monotonic()
    out = vux_tool.linux(ser, timeout=LOAD_TIMEOUT)
    times["linux to [LINUX] (reading the card)"] = time.monotonic() - t
    line = next(ln for ln in out.splitlines() if ln.startswith("[LINUX] "))
    print(line)
    want = f"[LINUX] Image {len(image)} B, DTB {len(dtb)} B, starting at 0x80000000"
    if line != want:
        failures.append(f"the card's files are not the tree's: {line!r}, expected {want!r}")
    t = time.monotonic()
    console = read_until(ser, linux_checks.PROMPT, BOOT_TIMEOUT, vux_tool.after_run(out, "[LINUX] "))
    times["[LINUX] to the shell's prompt (the boot)"] = time.monotonic() - t
    times["bitstream load to the shell's prompt"] = time.monotonic() - t0
    lost = linux_checks.missing(console, linux_checks.BOOT_LINES)
    if lost:
        failures.append(f"boot lines missing: {lost}")
    version = banner(image)
    if version is None or version not in console:
        failures.append(f"not the tree's kernel: the console lacks its {version or 'Linux version'!r} line")
    print(console.rstrip())
    probes = [run_command(ser, cmd) for cmd in linux_checks.MEM_PROBES]
    try:
        if None in probes:
            raise ValueError("no echo of a probe")
        mem.update(linux_checks.memory(probes[0] or "", probes[1] or "", sizes))
    except ValueError as e:
        failures.append(f"memory: {e}: {probes!r}"[:600])
    for cmd, wanted in linux_checks.COMMANDS.items():
        t = time.monotonic()
        reply = run_command(ser, cmd)
        times[f"command: {cmd}"] = time.monotonic() - t
        if reply is None:
            failures.append(f"{cmd}: no echo")
            continue
        print((cmd + reply).rstrip())
        lost = linux_checks.missing(reply, wanted)
        if lost:
            failures.append(f"{cmd}: missing {lost}")
    return failures


def card_files(linux_dir: str) -> tuple[bytes, bytes, dict[str, int]]:
    """The Image and the device tree of a make linux-dist tree, checked against its
    MANIFEST.json, and the build's sizes it records (for the memory table)."""
    with open(os.path.join(linux_dir, "MANIFEST.json")) as f:
        manifest = json.load(f)
    datas = []
    for rel in CARD_FILES:
        with open(os.path.join(linux_dir, rel), "rb") as f:
            data = f.read()
        if len(data) != manifest["card_files"][rel]["size"]:
            sys.exit(f"hw_linux: {rel} is not the size its MANIFEST.json gives")
        datas.append(data)
    print(f"[INFO] kernel: {manifest['git_describe']}, Linux {manifest['linux_version']}")
    return datas[0], datas[1], manifest.get("sizes", {})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--linux", metavar="DIR", default=os.path.join(REPO_ROOT, "build", "linux-dist"), help="make linux-dist's tree"
    )
    parser.add_argument("--dist", metavar="DIR", help="a `make dist` tree: its pack.fs")
    parser.add_argument("--pack-fs", help="the bitstream to load (default: --dist's, else build/synth/pack.fs)")
    parser.add_argument("--loader", default="openFPGALoader", help="openFPGALoader to load it with")
    parser.add_argument("--port", default="auto", help="serial port, or socket://HOST:PORT for the emulator")
    parser.add_argument("--keep-card", action="store_true", help="boot the files already on the card, no put")
    return parser.parse_args(argv)


def pack_fs_of(args) -> str:
    if args.pack_fs:
        return args.pack_fs
    if args.dist:
        return os.path.join(args.dist, DIST_FILES["pack_fs"])
    return os.path.join(REPO_ROOT, "build", "synth", "pack.fs")


def main(argv=None):
    args = parse_args(argv)
    image, dtb, sizes = card_files(args.linux)
    emulator = str(args.port).startswith("socket://")
    pack_fs = pack_fs_of(args)
    loader = None if emulator else find_loader(args.loader)

    def load(ser):
        """The bitstream into SRAM, the port opened again: (ser, the time the load ended)."""
        if emulator:
            return ser, time.monotonic()
        ser.close()
        err = load_bitstream(loader, pack_fs)
        if err:
            raise RuntimeError(err)
        t0 = time.monotonic()
        return reopen_port(args.port, 115200), t0

    ser = vux_tool.open_port(args.port, baudrate=115200, timeout=0.1)
    if not emulator:
        neighbours = usb_hub_neighbours(ser.port)
        if neighbours:
            print(f"[WARN] USB devices share the board's hub ({', '.join(neighbours)}); output can be lost")
        mm_hint = vux_tool.modemmanager_hint(ser.port)
        if mm_hint:
            ser.close()
            print(f"[FAIL] {mm_hint}")
            return 1
    times: dict[str, float] = {}
    mem: dict[str, str] = {}
    failures = []
    t0 = time.monotonic()
    try:
        if not args.keep_card:
            # Whatever runs (Linux from an earlier run too): the first stage back, then its
            # second stage's prompt
            ser, _ = load(ser)
            vux_tool.enter_s2(ser, timeout=S2_TIMEOUT)
            put_files(ser, dict(zip(CARD_FILES, (image, dtb), strict=True)), times)
            if emulator:
                # No bitstream to load: back to the first stage, which boots the card's second
                ser.write(b"reset\r")
                ser.flush()
                t0 = time.monotonic()
        if not emulator:
            ser, t0 = load(ser)
        failures = boot(ser, t0, image, dtb, times, sizes, mem)
    except Exception as e:  # the port gone too: the bitstream is still loaded and the times printed
        hint = vux_tool.garbled_hint(str(e))
        failures.append(f"{type(e).__name__}: {e}" + (f"\n       -> {hint}" if hint else ""))
    finally:
        try:
            ser.close()
        except Exception:
            pass
        if emulator:
            print("[INFO] the emulator: no bitstream to load")
        else:
            err = load_bitstream(loader, pack_fs)
            if err:
                print(f"[WARN] {err}: the board stays in Linux until the bitstream is loaded")
    for label, secs in times.items():
        print(f"[TIME] {label}: {secs:.1f} s")
    for name, value in mem.items():
        print(f"[MEM] {name}: {value}")
    for f in failures:
        print(f"[FAIL] {f}")
    print("Linux on the board: " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
