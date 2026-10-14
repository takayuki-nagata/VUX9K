#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Automated Hardware Test Suite (test_hardware.py)
End-to-end checks of the two-stage boot on a Sipeed Tang Nano 9K with a FAT32 MicroSD card
(README, "Real Hardware Test Suite", lists the tests): the first stage's prompt and card
report, a RAM boot of the second stage and its refusals, installing second stages on the
card (\\VUX9K\\STAGE2.BIN, the previous one kept as STAGE2.OLD), the fall back to
STAGE2.OLD, and the soft reset back to the first stage. It writes only in \\VUX9K\\,
which must exist on the card, and leaves the second stage under test installed there.
"""

import argparse
import json
import os
import re
import sys

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
}
DIST_FILES = {
    "pack_fs": "bitstream/pack.fs",
    "stage2": "sd/VUX9K/STAGE2.BIN",
}
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
    status = "\033[92m[PASS]\033[0m" if passed else "\033[91m[FAIL]\033[0m"
    print(f"{status} {name}")
    if detail:
        print(f"       -> {detail}")


def run_hardware_test_suite(port="auto", baud=115200):
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
    v_a, v_b, v_c = TEST_VERSIONS
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
            if "[INSTALL] not found" in msg:
                msg += " -> create the directory VUX9K in the card's root (on a PC) and run again"
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
    finally:
        try:
            ser.close()
        except Exception:
            pass

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print_banner("Hardware Test Suite Summary")
    total_tests = len(results)
    passed_tests = sum(1 for _, passed, _ in results if passed)
    print(f"Total Tests : {total_tests}")
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
    args = parser.parse_args()

    if args.dist:
        S1_VERSION = dist_stage1_version(args.dist)
        for key, rel in DIST_FILES.items():
            FILES[key] = os.path.join(args.dist, rel)
            if not os.path.isfile(FILES[key]):
                sys.exit(f"{FILES[key]} missing: not a `make dist` tree?")

    success = run_hardware_test_suite(port=args.port, baud=args.baud)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
