#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Automated Hardware Test Suite (test_hardware.py)
End-to-end checks of the two-stage boot on a Sipeed Tang Nano 9K with a FAT32 MicroSD card
(README, "Real Hardware Test Suite", lists the tests): the first stage's prompt and card
report, a RAM boot of the second stage and its refusals, installing second stages on the
card (\\VUX9K\\STAGE2.BIN, the previous one kept as STAGE2.OLD; install makes \\VUX9K\\
if it is missing), the fall back to STAGE2.OLD, the soft reset back to the first stage, the
second stage's file commands, and the demos started by its `run`: HELLO.BIN in the PSRAM,
then the three that never return to the first stage, HACK.BIN in I-RAM (it replaces the first
stage), ZEPHYR.BIN and BC.BIN in the PSRAM (they leave the first stage in I-RAM, but only the
reset button would start it, and the suite can't press it), each followed by a load of the
bitstream into the FPGA's SRAM (openFPGALoader, never --reset: that loads the flash's
bitstream). The card stays in the board. The suite writes only in \\VUX9K\\: test versions
9001-9003, \\VUX9K\\TEST\\ (removed again), the demos in \\VUX9K\\APPS\\ (left there, the
card's documented layout), and last the second stage under test as STAGE2.BIN and STAGE2.OLD.
Against the emulator (`--port socket://...`) the tests that need a bitstream load are skipped.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)


def stage1_version():
    """STAGE1_VERSION of the first stage being tested (it hands it to the second in a2)."""
    with open(os.path.join(REPO_ROOT, "firmware", "stage1", "src", "main.rs")) as f:
        m = re.search(r"const STAGE1_VERSION: u32 = (\d+);", f.read())
    assert m, "STAGE1_VERSION not found in main.rs"
    return int(m.group(1))


def dist_stage1_version(dist):
    """The first stage's version a `make dist` tree ships (its MANIFEST.json)."""
    with open(os.path.join(dist, "MANIFEST.json")) as f:
        return int(json.load(f)["stage1_version"])


# With --dist, main() replaces it with the dist's version
S1_VERSION = stage1_version()

# What the suite uses: the local build outputs, or with --dist DIR a `make dist` tree
FILES = {
    "pack_fs": os.path.join(REPO_ROOT, "build", "synth", "pack.fs"),
    "stage2": os.path.join(REPO_ROOT, "build", "firmware", "stage2", "STAGE2.BIN"),
    "apps": os.path.join(REPO_ROOT, "build", "firmware", "apps"),
}
DIST_FILES = {
    "pack_fs": "bitstream/pack.fs",
    "stage2": "sd/VUX9K/STAGE2.BIN",
}
# The demos the second stage starts (make apps / the dist's sd/VUX9K/APPS/): name -> file.
# ZEPHYR.BIN and BC.BIN are missing from a build without Zephyr; their tests are skipped then.
APPS = ("HELLO.BIN", "HACK.BIN", "ZEPHYR.BIN", "BC.BIN")
DIST_APPS_DIR = "sd/VUX9K/APPS"
# Where on the card the suite puts its files
CARD_APPS = "VUX9K/APPS"
CARD_TEST = "VUX9K/TEST"
# What the demos print (the emulator's tests: sim/emu/test_hello_demo.py, test_hack_demo.py,
# test_zephyr_demo.py)
HELLO_LAST = "[HELLO] Press r for the first stage.\n"
HACK_LINES = (
    "VUX9K SoC Hack 16-bit C Firmware Test",
    "Factorial(6) = 720 ... [PASS]",
    "Fibonacci(10) = 55 ... [PASS]",
    "Array Sum = 150 ... [PASS]",
)
HACK_DONE = "ALL HACK C FIRMWARE TESTS PASSED (100%)!"
HACK_END = "=" * 41 + "\n"  # the closing rule after HACK_DONE
ZEPHYR_LINES = (
    "Zephyr RTOS Booting on VUX9K SoC!",
    "[Rust Task] Task iteration 5 completed [OK]",
    "All Rust application tasks finished successfully!",
)
ZEPHYR_END = "Entering sleep loop."
# The second stage's [RUN] line for it (and BC.BIN): the Zephyr board vux9k links into the PSRAM
ZEPHYR_RUN = "PSRAM, starting at 0x80000000"
# bc_clone_rs on Zephyr (sim/emu/test_zephyr_bc.py): its self-tests, then the REPL's prompt
# and lines typed there with what they print
BC_LINES = (
    "Basic Arithmetic & Precedence",
    "Scale Division",
    "BigInt Power (2^100)",
    "Recursive Factorial f(20)",
    "Transcendental Pi: 4 * a(1)",
    "Transcendental Exp: e(1)",
    "Transcendental Log: l(2.718281828459045)",
    "Base Conversion (Hex to Binary)",
    "Arrays and Dynamic Auto Scoping",
    "Streaming Callback Test: 2^16 = 65536",
    "ALL ZEPHYR BC_CORE TESTS PASSED (100%)!",
)
BC_PROMPT = "bc> "
BC_REPL = (("2^32", "4294967296"), ("define cube(x) { return (x^3); }", ""), ("cube(5)", "125"))
# bc is ~260 KB: `put` at 115200 baud takes ~23 s, the second stage's load from the card
# ~35 s (~2,400 cycles a byte), and the self-tests run from the PSRAM without a cache.
# Guesses with room, until the board's times (printed as [TIME]) are known. The put's
# bounds each transfer line and the card write after it (vux_tool.put)
BC_PUT_TIMEOUT = 90.0
BC_RUN_TIMEOUT = 120.0
BC_BOOT_TIMEOUT = 300.0
BC_LINE_TIMEOUT = 120.0
# Versions the suite installs, apart from what a release ships
TEST_VERSIONS = (9001, 9002, 9003)


def with_version(stage2, version, **kw):
    """The second stage file stage2 with another header version (and overrides)."""
    h = vux_tool.ImageHeader.parse(stage2)
    assert h, "not a VUX9 v4 file"
    data, _ = vux_tool.build_vux9_v4_image(
        stage2[vux_tool.HEADER_LEN :],
        name=h.name.rstrip(b"\0").decode(),
        version=version,
        load_addr=kw.pop("load_addr", h.load_addr),
        entry_point=kw.pop("entry_point", h.entry_point),
        **kw,
    )
    return data


def handoff(text):
    """a0, a1, a2 as the second stage printed them, or None."""
    m = re.search(r"\[S2\] a0=0x([0-9A-F]{8}) a1=0x([0-9A-F]{8}) a2=0x([0-9A-F]{8})", text)
    return tuple(int(g, 16) for g in m.groups()) if m else None


def hello_regs(text):
    """a0, a1, a2 as the hello demo printed them, or None."""
    m = re.search(r"\[HELLO\] a0=0x([0-9A-F]{8}) a1=0x([0-9A-F]{8}) a2=0x([0-9A-F]{8})", text)
    return tuple(int(g, 16) for g in m.groups()) if m else None


def ls_entries(out):
    """{name: size, or None for a directory} of the second stage's `ls` lines."""
    entries = {}
    for line in out.splitlines():
        name, col = line[:13].strip(), line[13:23].strip()
        if name:
            entries[name] = None if col == "<DIR>" else int(col)
    return entries


def remove_tree(ser, path):
    """Remove path on the card (a file, or a directory with all it holds) with the second
    stage's rm/rmdir; nothing if it isn't there."""
    try:
        out = vux_tool.s2_cmd(ser, f"ls {path}", timeout=30.0)
    except vux_tool.S2Error as e:
        if "not found" in str(e):
            return
        raise
    entries = ls_entries(out)
    leaf = path.rstrip("/").rsplit("/", 1)[-1].upper()
    if list(entries) == [leaf] and entries[leaf] is not None:  # a file lists itself
        vux_tool.s2_cmd(ser, f"rm {path}", timeout=30.0)
        return
    for name, size in entries.items():
        if size is None:
            remove_tree(ser, f"{path}/{name}")
        else:
            vux_tool.s2_cmd(ser, f"rm {path}/{name}", timeout=30.0)
    vux_tool.s2_cmd(ser, f"rmdir {path}", timeout=30.0)


def mkdir_p(ser, path):
    """mkdir path on the card unless it exists."""
    try:
        vux_tool.s2_cmd(ser, f"mkdir {path}", timeout=30.0)
    except vux_tool.S2Error as e:
        if "exists" not in str(e):
            raise


def find_loader(name="openFPGALoader"):
    """openFPGALoader as the Makefile's OPENFPGALOADER names it, on PATH or in the OSS CAD
    Suite's usual place; None if there is none."""
    found = shutil.which(name)
    if found:
        return found
    home_cad = os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")
    return home_cad if os.path.isfile(home_cad) else None


def load_bitstream(loader, pack_fs):
    """Load pack_fs into the FPGA's SRAM (as `make prog-sram`; not --reset, which loads
    the flash's bitstream): the first stage is back in I-RAM. Returns an error or None."""
    if not loader:
        return "no openFPGALoader (--loader)"
    if not os.path.isfile(pack_fs):
        return f"{pack_fs} missing"
    try:
        r = subprocess.run([loader, "-b", "tangnano9k", pack_fs], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{loader}: {e}"
    return None if r.returncode == 0 else f"{loader} failed: {(r.stderr or r.stdout).strip()[-400:]}"


def reopen_port(port, baud, timeout=15.0):
    """The board's UART opened again after a bitstream load (its tty may come back late)."""
    end = time.monotonic() + timeout
    while True:
        try:
            return vux_tool.open_port(port, baudrate=baud, timeout=0.1)
        except Exception:
            if time.monotonic() > end:
                raise
            time.sleep(0.5)


def usb_hub_neighbours(tty, sysfs="/sys"):
    """Full/low-speed USB devices sharing the board's hub, other than the board.

    The board's USB-UART bridge loses output when such a device is busy on the same
    USB 2.0 hub: it keeps 128 bytes, drops the rest and delivers the 128 bytes seconds
    later (docs/agents/clock-and-board.md, "Board UART output that stops"). Returns their names, or [] when
    the board is on a root port or the port isn't a USB tty.
    """
    try:
        dev = os.path.realpath(os.path.join(sysfs, "class", "tty", os.path.basename(os.path.realpath(tty)), "device"))
    except OSError:
        return []
    while dev != os.path.dirname(dev) and not os.path.isfile(os.path.join(dev, "idVendor")):
        dev = os.path.dirname(dev)
    hub = os.path.dirname(dev)
    if not os.path.isfile(os.path.join(dev, "idVendor")) or not os.path.isfile(os.path.join(hub, "idVendor")):
        return []  # not USB, or on a root hub: no transaction translator to share

    def read(d, name):
        try:
            with open(os.path.join(d, name)) as f:
                return f.read().strip()
        except OSError:
            return ""

    neighbours = []
    prefix = os.path.basename(hub) + "."
    for entry in sorted(os.listdir(hub)):
        d = os.path.join(hub, entry)
        if entry.startswith(prefix) and ":" not in entry and d != dev and read(d, "speed") in ("1.5", "12"):
            label = " ".join(x for x in (read(d, "manufacturer"), read(d, "product")) if x)
            neighbours.append(f"{label or 'unknown'} ({read(d, 'idVendor')}:{read(d, 'idProduct')})")
    return neighbours


def fail_msg(what, out):
    """A failure message with the output, and why it looks like noise when it does."""
    hint = vux_tool.garbled_hint(out)
    return f"{what} Output: {out!r}" + (f"\n       -> {hint}" if hint else "")


def print_banner(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_test_result(name, passed, detail=""):
    """passed: True, False, or None for a skipped test."""
    status = {True: "\033[92m[PASS]\033[0m", False: "\033[91m[FAIL]\033[0m", None: "\033[93m[SKIP]\033[0m"}[passed]
    print(f"{status} {name}")
    if detail:
        print(f"       -> {detail}")


def run_hardware_test_suite(port="auto", baud=115200, loader=None, reload=True):
    """The tests on the board at port (or the emulator at a socket:// port). loader is the
    openFPGALoader for the bitstream loads after the I-RAM demos; without reload (or on the
    emulator) those tests are skipped."""
    print_banner("VUX9K Real Hardware Test Suite (Tang Nano 9K + FAT32 MicroSD)")
    results = []

    def record(name, passed, msg):
        results.append((name, passed, msg))
        print_test_result(name, passed, msg)
        return passed

    try:
        ser = vux_tool.open_port(port, baudrate=baud, timeout=0.1)
    except Exception as e:
        print_test_result("0. Port Connection", False, str(e))
        return False

    neighbours = usb_hub_neighbours(ser.port)
    if neighbours:
        hub_hint = (
            "USB devices share the board's hub (" + ", ".join(neighbours) + "); they can make the board's "
            "USB-UART bridge drop output. Give the board its own USB port or hub."
        )
        print(f"\033[93m[WARN]\033[0m {hub_hint}")
    mm_hint = vux_tool.modemmanager_hint(ser.port)
    if mm_hint:
        ser.close()
        print_test_result("0. Host Setup", False, mm_hint)
        return False

    with open(FILES["stage2"], "rb") as f:
        stage2 = f.read()
    apps = {}
    for app in APPS:
        path = os.path.join(FILES["apps"], app)
        apps[app] = open(path, "rb").read() if os.path.isfile(path) else None
    v_a, v_b, v_c = TEST_VERSIONS
    emulator = str(port).startswith("socket://")
    no_reload = "the emulator has no bitstream to load" if emulator else None if reload else "--no-reload"
    # Why a bitstream load can't happen on this board (then the demos that need one aren't started)
    no_loader = "no openFPGALoader (--loader)" if not loader else None
    if not os.path.isfile(FILES["pack_fs"]):
        no_loader = f"{FILES['pack_fs']} missing"
    lost = None  # set once the board no longer answers: the tests after it fail with it

    def reload_board():
        """Load the bitstream into SRAM again (the first stage back in I-RAM) and see its
        banner after a soft reset this suite causes: (ok, message)."""
        nonlocal ser, lost
        try:
            ser.close()
        except Exception:
            pass
        err = load_bitstream(loader, FILES["pack_fs"])
        try:
            ser = reopen_port(port, baud)
        except Exception as e:
            lost = f"board UART gone after the bitstream load: {e}"
            return False, lost
        if err:
            lost = f"bitstream not loaded: {err}"
            return False, lost
        try:
            vux_tool.enter_s1(ser, timeout=30.0)
            ser.write(b"r")
            ser.flush()
            out = vux_tool.enter_s1(ser, drain=False)
        except TimeoutError as e:
            lost = "no first stage after the bitstream load"
            return False, fail_msg(lost + ".", str(e))
        want = f"VUX9K Stage 1 (v{S1_VERSION})"
        if want not in out:
            lost = "no first stage after the bitstream load"
            return False, fail_msg(f"No {want!r} banner after the bitstream load.", out)
        return True, f"bitstream loaded, {want} back"

    times: dict[str, float] = {}  # put_and_run's wall-clock seconds: "put", "run" (to the end of the [RUN] line)

    def put_and_run(app, put_timeout=20.0, run_timeout=30.0):
        """put apps[app] into \\VUX9K\\APPS\\ and `run` it: the [RUN] text."""
        vux_tool.enter_s2(ser)
        mkdir_p(ser, CARD_APPS)
        t0 = time.monotonic()
        vux_tool.put(ser, f"{CARD_APPS}/{app}", apps[app], timeout=put_timeout)
        t1 = time.monotonic()
        out = vux_tool.run_app(ser, f"{CARD_APPS}/{app}", timeout=run_timeout)
        times.update(put=t1 - t0, run=time.monotonic() - t1)
        return out

    def run_demo_then_reload(name, app, check, **timeouts):
        """Tests 12-14: a demo that never returns to the first stage (check(run_text) ->
        (ok, msg) reads its output), then the bitstream loaded again; skipped or not run
        where that load can't happen. timeouts go to put_and_run."""
        if no_reload:
            return record(name, None, f"skipped: {no_reload} (the demo never returns to the first stage)")
        if lost:
            return record(name, False, f"not run: {lost}")
        if no_loader:
            return record(name, False, f"not run: {no_loader}; the demo would keep the board until a load")
        try:
            ok, msg = check(put_and_run(app, **timeouts))
        except (vux_tool.UploadError, vux_tool.S2Error, TimeoutError) as e:
            ok, msg = False, str(e)
        back, back_msg = reload_board()
        return record(name, ok and back, f"{msg}; {back_msg}")

    try:
        # 1. The first stage's prompt, then its banner after a soft reset
        name = "1. First stage prompt and banner (vux_tool.enter_s1, 'r')"
        try:
            vux_tool.enter_s1(ser)
            ser.write(b"r")
            ser.flush()
            out = vux_tool.enter_s1(ser, drain=False)
            want = f"VUX9K Stage 1 (v{S1_VERSION})"
            ok = want in out
            msg = f"{want} at its prompt" if ok else fail_msg(f"No {want!r} banner.", out)
        except TimeoutError as e:
            ok, msg = False, fail_msg("No first-stage prompt.", str(e))
        if not record(name, ok, msg):
            print("\n[ERROR] Could not reach the first stage on the Tang Nano 9K.")
            return False

        # 2. The card: FAT32 and where
        name = "2. SD card and FAT32 volume ('i')"
        out = vux_tool.send_cmd_and_wait(ser, "i", timeout=30.0, prompt=vux_tool.S1_PROMPT)
        m = re.search(r"\[FAT\] FAT32 at LBA (\d+)", out)
        sdhc = "[SD] SDHC card" in out
        part_lba = int(m.group(1)) if m else None
        card = f"{'SDHC' if sdhc else 'SDSC'}, FAT32 at LBA {part_lba}"
        record(name, m is not None, card if m else fail_msg("No FAT32 volume.", out))

        # 3. RAM boot of the second stage
        name = "3. RAM boot of the second stage ('u' / vux_tool.ram_boot)"
        try:
            out = vux_tool.ram_boot(ser, stage2)
            got = handoff(out)
            ok = got == (int(sdhc), part_lba, S1_VERSION)
            msg = (
                f"a0-a2 = {got}"
                if ok
                else fail_msg(f"Handoff {got}, expected {(int(sdhc), part_lba, S1_VERSION)}.", out)
            )
        except (vux_tool.UploadError, TimeoutError) as e:
            ok, msg = False, str(e)
        record(name, ok, msg)

        # 4. What the first stage refuses to start
        name = "4. RAM boot refusals (magic, CRC, load window)"
        vux_tool.enter_s1(ser)
        cases = [
            (with_version(stage2, 1, magic_override=0x12345678), "not a VUX9 v4 file"),
            (with_version(stage2, 1, crc_override=0), "CRC mismatch"),
            (with_version(stage2, 1, load_addr=vux_tool.XMEM_BASE, entry_point=vux_tool.XMEM_BASE), "loads outside"),
        ]
        bad = []
        for data, reason in cases:
            try:
                vux_tool.ram_boot(ser, data)
                bad.append(f"{reason}: started")
                vux_tool.enter_s1(ser)
            except vux_tool.UploadError as e:
                if f"[BOOT] {reason}" not in str(e):
                    bad.append(f"{reason}: {e}")
        record(name, not bad, "each refused, still at the prompt" if not bad else "; ".join(bad))

        # 5. Install two second stages: the second keeps the first as STAGE2.OLD
        name = f"5. Install STAGE2.BIN v{v_a}, then v{v_b} (second stage 'install')"
        try:
            vux_tool.ram_boot(ser, stage2)
            vux_tool.install_stage2(ser, with_version(stage2, v_a))
            vux_tool.install_stage2(ser, with_version(stage2, v_b))
            ok, msg = True, "[INSTALL] OK twice"
        except (vux_tool.UploadError, TimeoutError) as e:
            ok, msg = False, str(e)
        record(name, ok, msg)

        # 6. Soft reset back to the first stage, which boots the new one
        name = f"6. Soft reset to the first stage, which boots v{v_b}"
        ser.write(b"reset\r")
        ser.flush()
        got, out = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 20.0)
        ok = got is not None and f"[BOOT] STAGE2.BIN v{v_b} " in out and "VUX9K Stage 1" in out
        record(name, ok, f"booted v{v_b}" if ok else fail_msg("Not booted.", out))

        # 7. The card as the first stage sees it
        name = f"7. STAGE2.BIN v{v_b} and STAGE2.OLD v{v_a} on the card ('i')"
        vux_tool.enter_s1(ser)
        out = vux_tool.send_cmd_and_wait(ser, "i", timeout=30.0, prompt=vux_tool.S1_PROMPT)
        ok = f"STAGE2.BIN: v{v_b} " in out and f"STAGE2.OLD: v{v_a} " in out
        record(name, ok, "both checked [OK]" if ok else fail_msg("Unexpected files.", out))

        # 8. A broken STAGE2.BIN: the first stage boots STAGE2.OLD
        name = "8. Broken STAGE2.BIN: boot from STAGE2.OLD (second stage 'install -u')"
        try:
            vux_tool.ram_boot(ser, stage2)
            vux_tool.install_stage2(ser, with_version(stage2, v_c, crc_override=0), unchecked=True)
            ser.write(b"reset\r")
            ser.flush()
            got, out = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 20.0)
            ok = got is not None and f"[BOOT] STAGE2.OLD (STAGE2.BIN missing or broken) v{v_a} " in out
            msg = f"booted STAGE2.OLD v{v_a}" if ok else fail_msg("No fall back.", out)
        except (vux_tool.UploadError, TimeoutError) as e:
            ok, msg = False, str(e)
        record(name, ok, msg)

        # 9. Leave the card with the second stage under test
        name = "9. Install the second stage under test and boot it"
        try:
            vux_tool.install_stage2(ser, stage2)
            ser.write(b"reset\r")
            ser.flush()
            h = vux_tool.ImageHeader.parse(stage2)
            got, out = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 20.0)
            ok = got is not None and h is not None and f"[BOOT] STAGE2.BIN v{h.version} " in out
            msg = "installed and booted" if ok else fail_msg("Not booted.", out)
        except (vux_tool.UploadError, TimeoutError) as e:
            ok, msg = False, str(e)
        record(name, ok, msg)

        # 10. The second stage's file commands, in \VUX9K\TEST\ only (removed again, also
        # after a failure; a leftover from an earlier run is removed first)
        name = "10. File commands in \\VUX9K\\TEST\\ (mkdir, put, ls, cat, mv, rm, rmdir)"
        text = "".join(f"line {i:03} of the VUX9K board test\n" for i in range(24))  # 792 B: two sectors
        data, sub = f"{CARD_TEST}/DATA.TXT", f"{CARD_TEST}/SUB"
        step = "the second stage's prompt"

        def expect(what, got, want):
            if got != want:
                raise vux_tool.S2Error(f"{what}: {got!r}, expected {want!r}")

        try:
            vux_tool.enter_s2(ser)
            step = "removing a leftover TEST tree"
            remove_tree(ser, CARD_TEST)
            step = "the commands"
            vux_tool.s2_cmd(ser, f"mkdir {CARD_TEST}", timeout=30.0)
            vux_tool.s2_cmd(ser, f"mkdir {sub}", timeout=30.0)
            vux_tool.put(ser, data, text.encode(), timeout=20.0)
            expect(
                "ls TEST",
                ls_entries(vux_tool.s2_cmd(ser, f"ls {CARD_TEST}", timeout=30.0)),
                {"DATA.TXT": len(text), "SUB": None},
            )
            expect("cat", vux_tool.s2_cmd(ser, f"cat {data}", timeout=30.0), text)
            vux_tool.s2_cmd(ser, f"mv {data} {sub}", timeout=30.0)
            expect("ls TEST after mv", ls_entries(vux_tool.s2_cmd(ser, f"ls {CARD_TEST}", timeout=30.0)), {"SUB": None})
            expect("cat in SUB", vux_tool.s2_cmd(ser, f"cat {sub}/DATA.TXT", timeout=30.0), text)
            vux_tool.s2_cmd(ser, f"mv {sub}/DATA.TXT {CARD_TEST}", timeout=30.0)
            expect("ls SUB after mv back", vux_tool.s2_cmd(ser, f"ls {sub}", timeout=30.0), "")
            expect("cat after mv back", vux_tool.s2_cmd(ser, f"cat {data}", timeout=30.0), text)
            vux_tool.s2_cmd(ser, f"rm {data}", timeout=30.0)
            vux_tool.s2_cmd(ser, f"rmdir {sub}", timeout=30.0)
            vux_tool.s2_cmd(ser, f"rmdir {CARD_TEST}", timeout=30.0)
            left = ls_entries(vux_tool.s2_cmd(ser, "ls VUX9K", timeout=30.0))
            ok = "TEST" not in left
            msg = f"{len(text)} B put, read back, moved and removed" if ok else f"TEST still there: {left}"
        except (vux_tool.UploadError, vux_tool.S2Error, TimeoutError) as e:
            ok, msg = False, f"{step}: {e}"
            try:
                remove_tree(ser, CARD_TEST)
            except (vux_tool.UploadError, vux_tool.S2Error, TimeoutError) as e2:
                msg += f"; and removing TEST: {e2}"
        record(name, ok, msg)

        # 11. A PSRAM application: the first stage stays in I-RAM, `r` goes back to it
        name = "11. HELLO.BIN in the PSRAM ('put', 'run'): a0-a2, 'r' to the first stage and its autoboot"
        if apps["HELLO.BIN"] is None:
            record(name, False, f"{os.path.join(FILES['apps'], 'HELLO.BIN')} missing (make apps)")
        else:
            try:
                out = put_and_run("HELLO.BIN")
                got, out2 = vux_tool.read_until(ser, (HELLO_LAST,), 10.0, vux_tool.after_run(out))
                regs, expected = hello_regs(out2), (int(sdhc), part_lba, S1_VERSION)
                ser.write(b"r")
                ser.flush()
                back, out3 = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 30.0)
                banner = f"VUX9K Stage 1 (v{S1_VERSION})"
                if ", PSRAM, " not in out or not got:
                    ok, msg = False, fail_msg("Not started in the PSRAM.", out + out2)
                elif regs != expected:
                    ok, msg = False, fail_msg(f"a0-a2 = {regs}, expected {expected}.", out2)
                elif not back or "[HELLO] Soft reset" not in out3 or banner not in out3:
                    ok, msg = False, fail_msg("No first stage and second stage after 'r'.", out3)
                else:
                    ok, msg = True, f"a0-a2 = {regs}, back to {banner} and s2>"
            except (vux_tool.UploadError, vux_tool.S2Error, TimeoutError) as e:
                ok, msg = False, str(e)
            record(name, ok, msg)

        # 12-14. Applications that never return to the first stage (HACK.BIN replaces it in
        # I-RAM; ZEPHYR.BIN and BC.BIN run in the PSRAM and leave it, but only the reset
        # button would start it): last, each followed by a load of the bitstream into SRAM
        # (not --reset: that loads the flash's)
        def hack_check(out):
            if "I-RAM (Hack)" not in out:
                return False, fail_msg("Not started in I-RAM (Hack).", out)
            _, rest = vux_tool.read_until(ser, (HACK_DONE,), 20.0, vux_tool.after_run(out))
            _, tail = vux_tool.read_until(ser, (HACK_END,), 3.0)
            rest += tail
            missing = [ln for ln in (*HACK_LINES, HACK_DONE) if ln not in rest]
            if missing or "[FAIL]" in rest or not rest.endswith(HACK_END):
                return False, fail_msg(f"Missing {missing} or a [FAIL].", rest)
            return True, "the Hack demo's report, all [PASS]"

        run_demo_then_reload("12. HACK.BIN in I-RAM (Hack), then the bitstream loaded again", "HACK.BIN", hack_check)

        def zephyr_check(out):
            if ZEPHYR_RUN not in out:
                return False, fail_msg("Not started in the PSRAM.", out)
            _, rest = vux_tool.read_until(ser, (ZEPHYR_END,), 30.0, vux_tool.after_run(out))
            missing = [ln for ln in (*ZEPHYR_LINES, ZEPHYR_END) if ln not in rest]
            m = re.search(r"k_msleep\(100\) took (\d+) ms", rest)
            # A tick (1 ms at 1000 ticks/s) plus the kernel's own time from the uncached
            # PSRAM: 103-104 ms in simulation (102 from I-RAM, again with a cache in front of the PSRAM);
            # 100 ticks/s reads 110 or more. The board has no expected failures: the
            # value is in the result line to compare.
            if missing or "VUX9K Stage" in rest or not m or not 100 <= int(m.group(1)) <= 105:
                return False, fail_msg(f"Missing {missing}, or k_msleep(100) off.", rest)
            return True, f"the Zephyr demo's tasks finished, k_msleep(100) took {m.group(1)} ms"

        name = "13. ZEPHYR.BIN in the PSRAM, then the bitstream loaded again"
        if apps["ZEPHYR.BIN"] is None:
            record(name, None, f"skipped: no ZEPHYR.BIN in {FILES['apps']} (built without Zephyr)")
        else:
            run_demo_then_reload(name, "ZEPHYR.BIN", zephyr_check)

        def bc_check(out):
            """bc's self-tests and REPL, with the wall-clock time of each step ([TIME] lines:
            the board's numbers next to the emulator's cycle counts in test_zephyr_bc.py)."""
            if ZEPHYR_RUN not in out:
                return False, fail_msg("Not started in the PSRAM.", out)
            print(f"  [TIME] BC.BIN put {times['put']:.1f} s, run to the [RUN] line {times['run']:.1f} s")
            t0 = time.monotonic()
            got, rest = vux_tool.read_until(ser, (BC_PROMPT,), BC_BOOT_TIMEOUT, vux_tool.after_run(out))
            print(f"  [TIME] [RUN] line to the first {BC_PROMPT!r} {time.monotonic() - t0:.1f} s")
            missing = [ln for ln in BC_LINES if ln not in rest]
            if not got or missing or "VUX9K Stage" in rest:
                return False, fail_msg(f"No prompt, or missing {missing}.", rest)
            for line, want in BC_REPL:
                # Paced like a person typing: the RX FIFO holds 32 bytes, the define line is longer
                t0 = time.monotonic()
                for i in range(0, len(line), 8):
                    ser.write(line[i : i + 8].encode())
                    ser.flush()
                    time.sleep(0.05)
                ser.write(b"\r")
                ser.flush()
                got, reply = vux_tool.read_until(ser, (BC_PROMPT,), BC_LINE_TIMEOUT)
                print(f"  [TIME] {line!r} to the next {BC_PROMPT!r} {time.monotonic() - t0:.1f} s")
                if not got or want not in reply:
                    return False, fail_msg(f"{line!r}: no {want!r} and prompt.", reply)
            return True, "bc's self-tests passed, 2^32 and a define at its prompt"

        name = "14. BC.BIN in the PSRAM, then the bitstream loaded again"
        if apps["BC.BIN"] is None:
            record(name, None, f"skipped: no BC.BIN in {FILES['apps']} (built without Zephyr)")
        else:
            run_demo_then_reload(name, "BC.BIN", bc_check, put_timeout=BC_PUT_TIMEOUT, run_timeout=BC_RUN_TIMEOUT)

        # 15. Last, after the loads: the second stage under test installed (STAGE2.BIN, and
        # STAGE2.OLD the same) and booted by the bitstream's first stage
        name = "15. Install the second stage under test again and boot it (the state left behind)"
        if lost:
            record(name, False, f"not run: {lost}")
        else:
            try:
                vux_tool.enter_s2(ser)
                vux_tool.install_stage2(ser, stage2)
                ser.write(b"reset\r")
                ser.flush()
                h = vux_tool.ImageHeader.parse(stage2)
                got, out = vux_tool.read_until(ser, (vux_tool.S2_PROMPT,), 20.0)
                ok = got is not None and h is not None and f"[BOOT] STAGE2.BIN v{h.version} " in out
                msg = "installed and booted" if ok else fail_msg("Not booted.", out)
            except (vux_tool.UploadError, TimeoutError) as e:
                ok, msg = False, str(e)
            record(name, ok, msg)
    finally:
        try:
            ser.close()
        except Exception:
            pass

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print_banner("Hardware Test Suite Summary")
    skipped = sum(1 for _, passed, _ in results if passed is None)
    total_tests = len(results) - skipped
    passed_tests = sum(1 for _, passed, _ in results if passed)
    print(f"Total Tests : {total_tests} (and {skipped} skipped)")
    print(f"Passed      : {passed_tests}")
    print(f"Failed      : {total_tests - passed_tests}")
    print(f"Score       : {passed_tests}/{total_tests} ({(passed_tests / total_tests) * 100:.1f}%)")

    if passed_tests == total_tests:
        print("\n\033[92m========================================================================")
        print("  ALL TANG NANO 9K HARDWARE & SD CARD TOOL TESTS PASSED 100%!")
        print("========================================================================\033[0m\n")
        return True
    else:
        print("\n\033[91m========================================================================")
        print("  SOME HARDWARE TESTS FAILED!")
        print("========================================================================\033[0m\n")
        return False


def main():
    global S1_VERSION
    parser = argparse.ArgumentParser(description="VUX9K Automated Hardware Test Suite")
    parser.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--dist", metavar="DIR", help="use the files of a `make dist` tree instead of build/")
    parser.add_argument(
        "--loader", default="openFPGALoader", help="openFPGALoader for the bitstream loads (default: on PATH)"
    )
    parser.add_argument("--no-reload", action="store_true", help="skip the demos that need a bitstream load afterwards")
    args = parser.parse_args()

    if args.dist:
        S1_VERSION = dist_stage1_version(args.dist)
        for key, rel in DIST_FILES.items():
            FILES[key] = os.path.join(args.dist, rel)
            if not os.path.isfile(FILES[key]):
                sys.exit(f"{FILES[key]} missing: not a `make dist` tree?")
        FILES["apps"] = os.path.join(args.dist, DIST_APPS_DIR)
        if not os.path.isdir(FILES["apps"]):
            sys.exit(f"{FILES['apps']} missing: not a `make dist` tree?")

    success = run_hardware_test_suite(
        port=args.port, baud=args.baud, loader=find_loader(args.loader), reload=not args.no_reload
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
