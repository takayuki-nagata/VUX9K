#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The noMMU Linux kernel (make linux-dist, or CI's vux9k-linux-<sha>) booted on the board to
busybox's shell, timed from the bitstream's load: its Image and vux9k.dtb put on the card as
\\VUX9K\\LINUX\\Image and vux9k.dtb, its programs for the card in \\VUX9K\\LINUX\\BIN\\,
through the second stage (`put`, the files left there; --keep-card uses those already there),
then pack.fs loaded into the FPGA's SRAM, the first stage booting the card's second stage by
itself, whose `linux` loads both files and starts the kernel. The console must show
sim/integration/linux_checks.py's boot lines and answer its commands, as on the emulator
(sim/emu/test_linux.py; the kernel mounts the card, runs programs and the sample scripts from
it and writes files, which linux_checks.CARD_CLEANUP removes again); right after the boot the
shell's builtins read /proc/meminfo and /proc/buddyinfo, and [MEM] lines give linux_checks'
memory table. Then vi is driven by linux_checks.VI_SESSIONS' keys and cat shows what each
session left, the card is cleaned up and unmounted, and last a paste at the shell is sent in
one write and as vux_tool.py's monitor paces it: [PASTE] lines give how many of its lines came
through (the paced one must pass them all). Linux never returns to the first stage, so pack.fs
is loaded once more at the end (not openFPGALoader --reset, which loads the flash's
bitstream). [TIME] lines give the wall-clock seconds: the put, the load to the second stage's
prompt, `linux` to its [LINUX] line (reading the card), [LINUX] to the shell's prompt (the
kernel's boot), the bitstream's load to that prompt, each command, each of the card's
busyboxes' first exec (its first command), and vi's sessions. With --dist the bitstream comes
from a `make dist` tree. Against the emulator (--port socket://...) there is no bitstream to
load: the times start when the port opens. Needs a card with the second stage on it (as make
test-hw leaves it); the card stays in the board.
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
CARD_BIN = "VUX9K/LINUX/BIN"
# make linux-dist's tree: the kernel's files (as they go on the card); its manifest's
# card_files also lists the programs for the card in CARD_BIN
CARD_FILES = ("VUX9K/LINUX/Image", "VUX9K/LINUX/vux9k.dtb")
# Wall-clock bounds, not measurements: on the emulator the Image's load took ~13 s and the
# boot ~12.5 s at 18 MHz, a command ~1 s (docs/agents/linux.md, "Speed"); a put of the
# 3.2 MB Image ~6 min at ~9 KB/s. A command's first exec of a program on the card reads it
# from there, ~3 times as slowly as on the emulator: ~15 s for the card's largest busybox
# (docs/agents/linux.md, "Text tools and vi"), and a command may run two
S2_TIMEOUT = 30.0
# The first stage boots the second ~0.7 s after the load (the board, 2026-11-11)
S2_PROMPT_WAIT = 5.0
LOAD_TIMEOUT = 120.0
BOOT_TIMEOUT = 180.0
COMMAND_TIMEOUT = 60.0
PUT_LINE_TIMEOUT = 30.0
# The commands before which the test waits for a free block of linux_checks.RECLAIM_ORDER (an
# exec of busybox after the card's reads: sysmon's reclaim), as test_linux.py's tests of them do
WAIT_LARGE = frozenset((*linux_checks.TOOL_COMMANDS, *linux_checks.PIPE_COMMANDS, *linux_checks.SCRIPT_COMMANDS))
# send_line's chunk of a command line, sent once the one before has come back as echo, at most
# ECHO_TIMEOUT after it (the shell echoes each key as it reads it)
SEND_CHUNK = 8
ECHO_TIMEOUT = 10.0
# What run_command types when a chunk's echo is missing: ^C, on which busybox's line editing
# drops the line and the shell prints its prompt again
CANCEL_LINE = b"\x03"
# type_line's gap from one chunk to the next, for the line after a paste
SEND_GAP_S = 0.02
# The driver's next polls of a paste's last bytes (HZ 100) before PASTE_DONE is typed
PASTE_SETTLE_S = 0.1


def read_until(ser, token: str, timeout: float, text: str = "") -> str:
    """The console's text up to and including token, which is looked for without the kernel's
    lines (linux_checks.KERNEL_LINE: a printk can split it); the text as it came, those lines
    kept. Raises TimeoutError with what came."""
    end = time.monotonic() + timeout
    while token not in linux_checks.without_kernel_lines(text):
        if time.monotonic() >= end:
            raise TimeoutError(f"{token!r} not received within {timeout} s; got {text[-600:]!r}")
        c = ser.read(256)
        if c:
            text += c.decode("utf-8", errors="replace")
    return text


def send_line(ser, line: str) -> str:
    """A command line at the shell's prompt in SEND_CHUNK-byte chunks, each sent once the
    console has echoed the line up to it: Linux's liteuart driver polls the 32-byte RX FIFO on
    the timer tick (HZ 100), so a long line sent back to back could overrun it, and the host's
    clock doesn't say when its bytes reach the UART (the emulator's CLI hands what came while
    the host had it descheduled to the UART at once). The LF's echo isn't waited for, and a
    kernel line in the echo is skipped (read_until). Returns
    what the console sent meanwhile, for the caller's reads; raises TimeoutError without the
    echo."""
    data = line + "\n"
    text = ""
    for i in range(0, len(data), SEND_CHUNK):
        ser.write(data[i : i + SEND_CHUNK].encode())
        ser.flush()
        if i + SEND_CHUNK < len(data):
            echo = data[: i + SEND_CHUNK]
            try:
                text = read_until(ser, echo, ECHO_TIMEOUT, text)
            except TimeoutError as e:
                raise TimeoutError(f"no echo of {echo!r} within {ECHO_TIMEOUT} s; got {text[-600:]!r}") from e
    return text


def type_line(ser, line: str) -> None:
    """A line in SEND_CHUNK-byte chunks SEND_GAP_S apart, no echo awaited: after a paste,
    whose lost bytes may have left a line open or its output still coming."""
    data = (line + "\n").encode()
    for i in range(0, len(data), SEND_CHUNK):
        ser.write(data[i : i + SEND_CHUNK])
        ser.flush()
        time.sleep(SEND_GAP_S)


def put_files(ser, files: dict[str, bytes], times: dict[str, float]) -> None:
    """At the second stage's prompt: the kernel's files onto the card in \\VUX9K\\LINUX\\, the
    programs in its BIN\\."""
    mkdir_p(ser, "VUX9K")
    mkdir_p(ser, CARD_LINUX)
    if any(path.startswith(CARD_BIN + "/") for path in files):
        mkdir_p(ser, CARD_BIN)
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
    without the echo (a chunk's echo missing: the line is dropped with ^C, so the run goes on
    to its next command). The kernel's lines in it are printed and left out of both."""
    try:
        text = send_line(ser, cmd)
    except TimeoutError as e:
        print(f"[WARN] {e}")
        ser.write(CANCEL_LINE)
        ser.flush()
        read_until(ser, linux_checks.PROMPT, COMMAND_TIMEOUT)
        return None
    # The echo, the reply and the next prompt can come in one read: read to the prompt
    out = read_until(ser, linux_checks.PROMPT, COMMAND_TIMEOUT, text)
    for line in linux_checks.KERNEL_LINE.findall(out):
        print(line.rstrip())
    out = linux_checks.without_kernel_lines(out)
    at = out.find(cmd)
    return None if at < 0 else out[at + len(cmd) :]


def wait_large(ser, before: str) -> str | None:
    """Waits up to RECLAIM_WAIT_S for a free block of RECLAIM_ORDER, which the next exec of
    busybox needs (sysmon gives one back after the card's reads; test_linux.py's wait_large),
    reading /proc/buddyinfo with the shell's builtins: the failure, None once one is free."""
    end = time.monotonic() + linux_checks.RECLAIM_WAIT_S
    while True:
        out = run_command(ser, linux_checks.MEM_PROBES[1])
        orders = linux_checks.parse_buddyinfo(out or "")
        if not orders:
            return f"{before}: no /proc/buddyinfo before it: {out!r}"[:600]
        if sum(orders[linux_checks.RECLAIM_ORDER :]):
            return None
        if time.monotonic() >= end:
            return (
                f"{before}: no free block of order {linux_checks.RECLAIM_ORDER} within "
                f"{linux_checks.RECLAIM_WAIT_S} s before it (free blocks per order {orders})"
            )
        time.sleep(1.0)


def boot(
    ser, t0: float, image: bytes, dtb: bytes, times: dict[str, float], sizes: dict[str, int], mem: dict[str, str]
) -> list[str]:
    """From the first stage's own boot of the second stage (started at t0) to the shell's
    prompt, then the memory table (into mem) and the commands, each of the card's busyboxes'
    first exec timed by its first command; returns the failures."""
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
        if cmd in WAIT_LARGE:
            err = wait_large(ser, cmd)
            if err:
                failures.append(err)
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
    # The first exec reads the busybox from the card, the later ones from the page cache
    for bb, cmd in linux_checks.first_execs(linux_checks.COMMANDS).items():
        size = f" ({sizes[bb]:,} B)" if bb in sizes else ""
        times[f"first exec of {bb}{size}: {cmd}"] = times[f"command: {cmd}"]
    return failures


def quiet(ser, seconds: float) -> str:
    """Reads until the console has printed nothing for `seconds` (vi idle, waiting for a key);
    what it read. Raises TimeoutError when it never goes quiet."""
    text = ""
    end = time.monotonic() + COMMAND_TIMEOUT
    last = time.monotonic()
    while time.monotonic() - last < seconds:
        if time.monotonic() > end:
            raise TimeoutError(f"the console never went quiet for {seconds} s; got {text[-600:]!r}")
        c = ser.read(256)
        if c:
            text += c.decode("utf-8", errors="replace")
            last = time.monotonic()
        else:
            time.sleep(0.01)
    return text


def vi_step(ser, step: str | tuple[str, ...], split: bool) -> float:
    """One of a ViSession's steps: its bytes in one write, a tuple's parts VI_SPLIT_S apart (all
    in one write without split); the longest time between two parts' writes as the host saw
    it (0 for one part).

    The board's console is its USB bridge (clock-and-board.md, "Board UART output that stops"):
    a write of fewer bytes than the UART's 32-byte RX FIFO goes out back to back, but when the
    bridge passes on each of a split step's parts is its own, so the gap between them on the
    wire is not the host's VI_SPLIT_S. vi takes `ESC [ A` as the arrow up while no gap passes its
    50 ms escape wait (linux_checks.VI_ESC_WAIT_S); a bridge that held a part longer would make
    it ESC alone, and the "keys" session's text would show it."""
    parts = step if isinstance(step, tuple) else (step,)
    if not split:
        parts = ("".join(parts),)
    longest = 0.0
    last = None
    for part in parts:
        if not 0 < len(part) < 32:
            raise ValueError(f"a vi step's part must fit the RX FIFO: {part!r}")
        if last is not None:
            time.sleep(max(0.0, last + linux_checks.VI_SPLIT_S - time.monotonic()))
            longest = max(longest, time.monotonic() - last)
        ser.write(part.encode())
        ser.flush()
        last = time.monotonic()
    return longest


def vi(ser, times: dict[str, float], split: bool = True) -> list[str]:
    """vi on linux_checks.VI_FILE driven by VI_SESSIONS' keys (VI_CLEAR before the first), as
    test_linux.py's test_vi does: `vi FILE`, its status line, then each step once the console
    has been quiet for VI_QUIET_S, the prompt after its :wq, and cat, which must show the
    session's text. After a session that fails, vi is left with :q! and the rest are skipped
    (each edits the file the one before left). split False sends a split step's parts in one
    write; returns the failures."""
    failures = []
    path = linux_checks.VI_FILE
    for k, session in enumerate(linux_checks.VI_SESSIONS):
        # Each exec after its own wait for a large block: rm's, then vi's
        if k == 0:
            err = wait_large(ser, linux_checks.VI_CLEAR)
            if err:
                failures.append(err)
            if run_command(ser, linux_checks.VI_CLEAR) is None:
                failures.append(f"{linux_checks.VI_CLEAR}: no echo")
        err = wait_large(ser, f"vi {session.name}")
        if err:
            failures.append(err)
        gap = 0.0
        try:
            t = time.monotonic()
            read_until(ser, session.ready, COMMAND_TIMEOUT, send_line(ser, f"vi {path}"))
            times[f"vi {session.name}: to its screen"] = time.monotonic() - t
            t = time.monotonic()
            for step in session.steps:
                quiet(ser, linux_checks.VI_QUIET_S)
                gap = max(gap, vi_step(ser, step, split))
            read_until(ser, linux_checks.PROMPT, COMMAND_TIMEOUT)
            times[f"vi {session.name}: its keys to the prompt"] = time.monotonic() - t
        except TimeoutError as e:
            failures.append(f"vi {session.name}: {e}")
            # Out of vi whatever mode it is in, back to the shell for the cleanup
            for keys in (linux_checks.ESC, ":q!\r"):
                quiet(ser, linux_checks.VI_QUIET_S)
                ser.write(keys.encode())
                ser.flush()
            vux_tool.read_until(ser, (linux_checks.PROMPT,), COMMAND_TIMEOUT)
            break
        err = wait_large(ser, f"cat {path}")
        if err:
            failures.append(err)
        reply = run_command(ser, f"cat {path}")
        # The file's lines after the echo's line end, then the prompt
        got = [ln.rstrip("\r") for ln in (reply or "").split("\n")]
        while got and not got[0]:
            got.pop(0)
        want = session.text.splitlines()
        print(f"vi {session.name}: {got[: len(want)]!r}")
        if got[: len(want)] != want or len(got) != len(want) + 1:
            msg = f"vi {session.name}: {path} holds {got!r}, expected {want!r}"
            if gap:
                msg += (
                    f" (its split step's parts went {gap * 1000:.0f} ms apart at most from the host; "
                    f"the USB bridge passing one on more than {linux_checks.VI_ESC_WAIT_S * 1000:.0f} ms "
                    "after the other makes vi read ESC alone: the bridge's timing, not vi's, when the "
                    "back-to-back sessions passed)"
                )
            failures.append(msg[:900])
            break
    return failures


def cleanup(ser) -> list[str]:
    """linux_checks.CARD_CLEANUP, then the mount table read with the shell's builtins: the card
    must be unmounted. Returns the failures."""
    failures = []
    for cmd in linux_checks.CARD_CLEANUP:
        if run_command(ser, cmd) is None:
            failures.append(f"{cmd}: no echo")
    mounts = run_command(ser, linux_checks.MOUNTS_PROBE)
    if mounts is None or linux_checks.CARD_MOUNTED in mounts:
        failures.append(f"the card is still mounted after {linux_checks.CARD_CLEANUP[-1]!r}: {mounts!r}"[:600])
    return failures


def paste(ser, pacer: vux_tool.Pacer | None) -> int:
    """linux_checks.PASTE_LINES at the shell in one write (pacer None: back to back at 115200
    baud from the bridge) or as vux_tool.py's monitor sends them (its Pacer on the host's
    clock), then PASTE_DONE once the driver has polled their last bytes: how many lines'
    output came through whole (the board counts no dropped bytes, as the emulator does)."""
    data = "".join(f"{line}\n" for line in linux_checks.PASTE_LINES).encode()
    if pacer is None:
        ser.write(data)
        ser.flush()
    else:
        pacer.push(data)
        for _ in range(4 * len(data)):  # a step cap: two turns a chunk
            wait = pacer.wait(time.monotonic())
            if wait is None:
                break
            if wait > 0:
                time.sleep(wait)
            else:
                ser.write(pacer.pop(time.monotonic()))
                ser.flush()
    time.sleep(PASTE_SETTLE_S)
    # A line a lost LF left open runs first (an echo, or a command not found), so PASTE_DONE
    # is a line of its own
    type_line(ser, "")
    type_line(ser, linux_checks.PASTE_DONE)
    out = linux_checks.without_kernel_lines(read_until(ser, linux_checks.PASTE_DONE_OUT, COMMAND_TIMEOUT))
    read_until(ser, linux_checks.PROMPT, COMMAND_TIMEOUT, out[out.index(linux_checks.PASTE_DONE_OUT) :])
    return sum(w in out for w in linux_checks.PASTE_WORDS)


def pastes(ser, pasted: dict[str, str]) -> list[str]:
    """The paste back to back, recorded only (the UART's RX FIFO drops bytes then: that is the
    SoC; docs/agents/linux.md, "Text tools and vi"), then paced, which must pass every line."""
    n = len(linux_checks.PASTE_LINES)
    size = sum(len(line) + 1 for line in linux_checks.PASTE_LINES)
    whole = paste(ser, None)
    pasted[f"{size} B back to back"] = f"{whole} of {n} lines whole"
    pacer = vux_tool.Pacer()
    whole = paste(ser, pacer)
    pasted[f"{size} B, {pacer.chunk} B {pacer.gap * 1000:.0f} ms apart (the monitor's)"] = f"{whole} of {n} lines whole"
    return [] if whole == n else [f"the paced paste: {whole} of {n} lines whole"]


def card_files(linux_dir: str) -> tuple[dict[str, bytes], dict[str, int]]:
    """Every card file of a make linux-dist tree ({path on the card: data}, CARD_FILES first),
    checked against its MANIFEST.json, and the build's sizes it records (for the memory table)."""
    with open(os.path.join(linux_dir, "MANIFEST.json")) as f:
        manifest = json.load(f)
    files = {}
    for rel in (*CARD_FILES, *sorted(set(manifest["card_files"]) - set(CARD_FILES))):
        with open(os.path.join(linux_dir, rel), "rb") as f:
            data = f.read()
        if len(data) != manifest["card_files"][rel]["size"]:
            sys.exit(f"hw_linux: {rel} is not the size its MANIFEST.json gives")
        files[rel] = data
    print(f"[INFO] kernel: {manifest['git_describe']}, Linux {manifest['linux_version']}")
    return files, manifest.get("sizes", {})


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
    files, sizes = card_files(args.linux)
    image, dtb = (files[rel] for rel in CARD_FILES)
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
    pasted: dict[str, str] = {}
    failures = []
    t0 = time.monotonic()
    try:
        if not args.keep_card:
            # Whatever runs (Linux from an earlier run too): the first stage back, then its
            # second stage's prompt
            ser, _ = load(ser)
            vux_tool.enter_s2(ser, timeout=S2_TIMEOUT)
            put_files(ser, files, times)
            if emulator:
                # No bitstream to load: back to the first stage, which boots the card's second
                ser.write(b"reset\r")
                ser.flush()
                t0 = time.monotonic()
        if not emulator:
            ser, t0 = load(ser)
        failures = boot(ser, t0, image, dtb, times, sizes, mem)
        if emulator:
            # The CLI's time is not the host's (--speed 0 runs faster): VI_SPLIT_S there is a
            # longer gap than vi's escape wait
            print("[INFO] the emulator: vi's split steps sent in one write")
        failures += vi(ser, times, split=not emulator)
        failures += cleanup(ser)
        failures += pastes(ser, pasted)
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
    for label, value in pasted.items():
        print(f"[PASTE] {label}: {value}")
    for f in failures:
        print(f"[FAIL] {f}")
    print("Linux on the board: " + ("FAILED" if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
