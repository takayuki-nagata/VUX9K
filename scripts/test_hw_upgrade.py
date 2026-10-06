#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Board test of the upgrade from a previous release: `make test-hw-upgrade OLD=<dir> NEW=<dir>`.

A release gate (docs/RELEASING.md, step 2): the slots and the tool of the previous release
must keep working with the candidate, and a board running the previous bitstream must
install the candidate's Boot Manager from slot 0. OLD and NEW are `make dist` trees (the
previous release's archive, the candidate); nothing is rebuilt. Both SHA256SUMS are
verified, the Boot Manager versions come from their MANIFEST.json, and each dist's
tools/vux_tool.py is loaded as its own module, so every flash, listing and boot command
is sent by the tool of the release it is about (the port is always opened with NEW's
open_port, which re-applies the line settings after the open). Steps:

  1. old state       OLD bitstream; OLD tool writes slot 0 = OLD Boot Manager, then
                     (under the OLD Boot Manager) slot 1 = OLD Zephyr demo, slot 2 = OLD
                     Hack demo; both boot
  2. new tool        NEW tool lists slots 0-2 and writes the OLD Zephyr demo to slot 3
                     under the OLD Boot Manager; it boots
  3. new bitstream   NEW bitstream: no "[UPDATE]" (slot 0 holds an older Boot Manager);
                     NEW tool lists the slots; slots 1, 2 and 3 boot
  4. old tool        OLD tool writes the OLD Zephyr demo to slot 4 under the NEW Boot
                     Manager; it boots
  5. BM update       NEW tool writes slot 0 = NEW Boot Manager; the OLD bitstream installs
                     it on every load ("[UPDATE] Verified ...", "Booted newly updated", NEW
                     banner); slots 1 and 2 boot (SKIP if the Boot Manager is unchanged,
                     FAIL if NEW's is older)
  6. final state     NEW bitstream, no "[UPDATE]"

Every check is PASS, FAIL or SKIP with a reason; a summary table ends the run and any
FAIL makes the exit status nonzero. The FPGA is only loaded into SRAM (openFPGALoader
-b tangnano9k), never into flash. Board state left behind: the NEW bitstream in SRAM; SD
slots 0-4 overwritten (slot 0 = the NEW Boot Manager, or the OLD one when step 5 is
skipped; slots 1-4 = the OLD demos).
"""

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from release_check import Mismatch, load_dist  # noqa: E402
from test_hardware import DIST_FILES, usb_hub_neighbours  # noqa: E402

import tools.vux_tool as vux_tool  # noqa: E402 (host checks: ModemManager, garbled output)

BANNER = re.compile(r"Boot Manager \(v(\d+)\)")
PROMPT = "vux>"
UPDATE_VERIFIED = "[UPDATE] Verified valid Boot Manager update (v{}"
UPDATE_BOOTED = "[UPDATE] Booted newly updated Boot Manager!"
ZEPHYR_MARKERS = ("[Rust App]", "All Rust application tasks finished successfully!")
HACK_MARKERS = ("Hack 16-bit C Firmware Test", "ALL HACK C FIRMWARE TESTS PASSED")
STARTUP_TIMEOUT = 20.0  # SD init, the slot-0 check and an update install take ~2 s
LOAD_TIMEOUT = 120.0
ZEPHYR_TIMEOUT = 20.0
HACK_TIMEOUT = 40.0  # test_hardware.py's test 15


def startup_segment(text):
    """This boot's output in text, up to its first prompt; None until the prompt arrived.

    text is what the board sent from the load on, which can still begin with the end of
    the previous image's output. The boot's own lines ("[UPDATE] ...", then the banner)
    come after the previous image's last prompt, and nothing of this boot is a prompt
    before its banner, so the segment starts after the last prompt before the last banner.
    """
    banners = list(BANNER.finditer(text))
    if not banners:
        return None
    b = banners[-1].start()
    if PROMPT not in text[b:]:
        return None
    s = text.rfind(PROMPT, 0, b)
    start = s + len(PROMPT) if s >= 0 else 0
    return text[start : text.index(PROMPT, b) + len(PROMPT)]


def check_startup(segment, version, update_to=None):
    """(ok, reason) for a startup segment: banner of version, and "[UPDATE]" only as expected.

    update_to: None for a boot that must not touch slot 0, else the version the Boot
    Manager in BRAM must verify and install before the banner.
    """
    if segment is None:
        return False, "no Boot Manager banner and prompt"
    m = BANNER.search(segment)
    assert m
    if int(m.group(1)) != version:
        return False, f"banner shows v{m.group(1)}, expected v{version}"
    if update_to is None:
        if "[UPDATE]" in segment:
            line = segment[segment.index("[UPDATE]") :].split("\n", 1)[0]
            return False, f"unexpected update: {line!r}"
        return True, f"banner v{version}, no [UPDATE]"
    verified = segment.find(UPDATE_VERIFIED.format(update_to))
    booted = segment.find(UPDATE_BOOTED)
    if verified < 0:
        return False, f"no {UPDATE_VERIFIED.format(update_to)!r}"
    if not verified < booted < m.start():
        return False, f"no {UPDATE_BOOTED!r} between the update and the banner"
    return True, f"installed v{update_to} from slot 0, banner v{version}"


def load_tool(path, name):
    """A dist's tools/vux_tool.py as module `name` (one per dist, side by side)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod


class Dist:
    def __init__(self, label, path):
        self.label, self.path = label, path
        manifest, _ = load_dist(path)  # raises Mismatch on a file not matching SHA256SUMS
        self.bm_version = int(manifest["boot_manager_version"])
        self.files = {key: os.path.join(path, rel) for key, rel in DIST_FILES.items()}
        self.files["tool"] = os.path.join(path, "tools", "vux_tool.py")
        for f in self.files.values():
            if not os.path.isfile(f):
                raise Mismatch(f"{f} missing: not a `make dist` tree?")
        self.tool = load_tool(self.files["tool"], f"vux_tool_{label.lower()}")


def tail(text, n=300):
    """The end of an output for a failure message, with the noise note when it is noise."""
    hint = vux_tool.garbled_hint(text)
    return f"output ...{text[-n:]!r}" + (f" ({hint})" if hint else "")


class Run:
    def __init__(self, old, new, port, loader):
        self.old, self.new, self.port, self.loader = old, new, port, loader
        self.ser: Any = None
        self.last_output = ""
        self.results: list[tuple[str, str, str, str, str]] = []  # (step, check, by, status, reason)
        self.step = ""

    def record(self, check, by, status, reason=""):
        self.results.append((self.step, check, by, status, reason))
        color = {"PASS": "92", "FAIL": "91", "SKIP": "93"}[status]
        print(f"\033[{color}m[{status}]\033[0m {self.step} / {check} ({by})")
        if reason:
            print(f"       -> {reason}")
        return status == "PASS"

    def open(self):
        if self.ser is not None:
            self.ser.close()
        self.ser = self.new.tool.open_port(self.port, baudrate=115200, timeout=0.05)
        return self.ser

    def reconfigure(self, dist):
        """Load dist's pack.fs into SRAM; this boot's startup output (None without a prompt)."""
        ser = self.open()
        # drop what the previous image sent (as hw_smoke.run_on_board): the bridge hands held
        # output over within milliseconds of the open; bounded, an application may still print
        stale_end = time.time() + 0.5
        while time.time() < stale_end:
            ser.read(4096)
        subprocess.run(
            [self.loader, "-b", "tangnano9k", dist.files["pack_fs"]],
            check=True,
            capture_output=True,
            timeout=LOAD_TIMEOUT,
        )
        buf, end = "", time.time() + STARTUP_TIMEOUT
        while time.time() < end:
            buf += ser.read(4096).decode("utf-8", errors="replace")
            if startup_segment(buf) is not None:
                break
        self.last_output = buf
        return startup_segment(buf)

    def load(self, dist, update_to=None):
        """Load dist's bitstream and check its startup: the running Boot Manager's version is
        dist's own, or update_to when the one in BRAM must install it from slot 0."""
        expect = update_to if update_to is not None else dist.bm_version
        seg = self.reconfigure(dist)
        ok, reason = check_startup(seg, expect, update_to)
        if not ok:
            reason += "; " + tail(seg if seg is not None else self.last_output)
        return self.record(f"load {dist.label} pack.fs", "openFPGALoader", "PASS" if ok else "FAIL", reason)

    def flash(self, dist, slot, src, key, name, mode, version=1):
        """Write src's file `key` (a DIST_FILES key) to slot with dist's tool."""
        meta = dist.tool.flash_slot(self.ser, src.files[key], slot=slot, name=name, mode=mode, version=version)
        return self.record(
            f"write slot {slot} = {src.label} {os.path.basename(src.files[key])}",
            f"{dist.label} tool",
            "PASS",
            f"{name!r}, {mode}, v{version}: {meta['num_sectors']} sectors, CRC 0x{meta['crc32']:08X}",
        )

    def list_slots(self, dist, expected):
        """List the slots with dist's tool; expected maps slot -> name."""
        out = dist.tool.list_slots(self.ser, timeout=8.0)
        missing = [
            f'{slot} "{name}"'
            for slot, name in expected.items()
            if not re.search(rf'Slot {slot} \(LBA \d+\): "{re.escape(name)}"', out)
        ]
        ok = not missing
        reason = f"slots {sorted(expected)} listed" if ok else f"missing {', '.join(missing)}; {tail(out)}"
        return self.record("list slots", f"{dist.label} tool", "PASS" if ok else "FAIL", reason)

    def boot(self, dist, slot, markers, timeout):
        """Boot slot with dist's tool and wait for the application's markers."""
        out = dist.tool.boot_slot(self.ser, slot=slot, timeout=2.0)
        end = time.time() + timeout
        while time.time() < end and not all(m in out for m in markers):
            out += self.ser.read(4096).decode("utf-8", errors="replace")
        needed = (f"[RL] Slot {slot}", *markers)
        missing = [m for m in needed if m not in out]
        ok = not missing
        reason = f"{markers[-1]!r}" if ok else f"missing {missing}; {tail(out)}"
        return self.record(f"boot slot {slot}", f"{dist.label} tool", "PASS" if ok else "FAIL", reason)

    def step_run(self, title, fn):
        self.step = title
        print(f"\n=== {title} ===")
        try:
            fn()
        except Exception as e:  # a failed flash, load or port: the next step starts with a load
            self.record("aborted", "-", "FAIL", f"{type(e).__name__}: {e}")


def run(old, new, port, loader):
    r = Run(old, new, port, loader)
    o, n = old, new
    names = {0: "Boot Manager", 1: "Zephyr (old tool)", 2: "Hack (old tool)"}
    names_3 = {**names, 3: "Zephyr (new tool)"}

    def step1():
        # slot 0 may hold a newer Boot Manager from an earlier run, which this first load
        # installs: write the OLD one first, so slots 1-2 are written under the OLD Boot Manager
        seg = r.reconfigure(o)
        r.record(
            f"load {o.label} pack.fs (any Boot Manager)",
            "openFPGALoader",
            "PASS" if seg is not None else "FAIL",
            "banner and prompt" if seg is not None else tail(r.last_output),
        )
        r.flash(o, 0, o, "boot_manager", names[0], "riscv", version=o.bm_version)
        r.load(o)
        r.flash(o, 1, o, "zephyr_demo", names[1], "riscv")
        r.flash(o, 2, o, "hack_demo", names[2], "hack")
        r.boot(o, 1, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)
        r.load(o)
        r.boot(o, 2, HACK_MARKERS, HACK_TIMEOUT)

    def step2():
        r.load(o)
        r.list_slots(n, names)
        r.flash(n, 3, o, "zephyr_demo", names_3[3], "riscv")
        r.boot(n, 3, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)

    def step3():
        r.load(n)
        r.list_slots(n, names_3)
        r.boot(n, 1, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)
        r.load(n)
        r.boot(n, 2, HACK_MARKERS, HACK_TIMEOUT)
        r.load(n)
        r.boot(n, 3, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)

    def step4():
        r.load(n)
        r.flash(o, 4, o, "zephyr_demo", "Zephyr (old tool, new BM)", "riscv")
        r.boot(o, 4, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)

    def step5():
        if n.bm_version < o.bm_version:  # a regression, or OLD and NEW swapped
            r.record("update", "-", "FAIL", f"NEW Boot Manager v{n.bm_version} is older than OLD v{o.bm_version}")
            return
        if n.bm_version == o.bm_version:
            r.record("update", "-", "SKIP", f"Boot Manager unchanged (OLD v{o.bm_version}, NEW v{n.bm_version})")
            return
        r.load(n)
        r.flash(n, 0, n, "boot_manager", names[0], "riscv", version=n.bm_version)
        r.load(o, update_to=n.bm_version)
        r.boot(n, 1, ZEPHYR_MARKERS, ZEPHYR_TIMEOUT)
        r.load(o, update_to=n.bm_version)
        r.boot(n, 2, HACK_MARKERS, HACK_TIMEOUT)

    def step6():
        r.load(n)

    try:
        r.step_run(f"1. old state (OLD v{o.bm_version} bitstream, OLD tool)", step1)
        r.step_run("2. NEW tool x OLD Boot Manager", step2)
        r.step_run(f"3. bitstream upgrade (NEW v{n.bm_version}, old slots)", step3)
        r.step_run("4. OLD tool x NEW Boot Manager", step4)
        r.step_run(f"5. Boot Manager update from slot 0 (OLD bitstream -> v{n.bm_version})", step5)
        r.step_run("6. final state: NEW bitstream", step6)
    finally:
        if r.ser is not None:
            r.ser.close()
    return r.results


def summary(results):
    print("\n" + "=" * 78 + "\n  Upgrade test summary\n" + "=" * 78)
    for step, check, by, status, reason in results:
        short = reason if len(reason) <= 70 else reason[:67] + "..."
        print(f"{status:<4}  {step.split('.', 1)[0]:>1}  {check[:44]:<44}  {by:<14}  {short}")
    counts = {s: sum(1 for r in results if r[3] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP")
    return counts["FAIL"] == 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--old", required=True, metavar="DIR", help="the previous release's `make dist` tree")
    p.add_argument("--new", required=True, metavar="DIR", help="the candidate's `make dist` tree")
    p.add_argument("--port", default="auto", help="serial port (default: auto)")
    p.add_argument("--loader", default="openFPGALoader", help="openFPGALoader to load the bitstreams with")
    args = p.parse_args()

    try:
        old, new = Dist("OLD", args.old), Dist("NEW", args.new)
    except (Mismatch, OSError, KeyError, ValueError) as e:
        sys.exit(f"test_hw_upgrade: {e}")
    print(f"OLD {args.old}: Boot Manager v{old.bm_version}\nNEW {args.new}: Boot Manager v{new.bm_version}")
    loader = shutil.which(args.loader) or os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")

    try:
        ser = new.tool.open_port(args.port, baudrate=115200, timeout=0.05)
    except Exception as e:
        sys.exit(f"test_hw_upgrade: no board: {e}")
    tty = ser.port
    ser.close()
    neighbours = usb_hub_neighbours(tty) if tty else []
    if neighbours:
        print(f"\033[93m[WARN]\033[0m USB devices share the board's hub ({', '.join(neighbours)}); output can be lost")
    mm_hint = vux_tool.modemmanager_hint(tty) if tty and "://" not in tty else ""
    if mm_hint:
        sys.exit(mm_hint)  # each load would let ModemManager change the board's baud rate

    ok = summary(run(old, new, args.port, loader))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
