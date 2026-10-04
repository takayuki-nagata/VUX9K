#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Automated Hardware Test Suite (test_hardware.py)
End-to-end checks on a Sipeed Tang Nano 9K with a MicroSD card, through the Boot
Manager's UART commands (README, "Real Hardware Test Suite", lists the 15 tests):
prompt, diagnostics, MBR dump, flashing and inspecting slots 0-2, the slot catalog,
CRC/magic rejection, Boot Manager self-update, and booting the Zephyr and Hack demos.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)


def boot_manager_version():
    """BOOT_MGR_VERSION of the Boot Manager being tested (tests 4, 11-13 flash slot 0 relative to it)."""
    with open(os.path.join(REPO_ROOT, "firmware", "boot_manager", "src", "main.rs")) as f:
        m = re.search(r"const BOOT_MGR_VERSION: u32 = (\d+);", f.read())
    assert m, "BOOT_MGR_VERSION not found in main.rs"
    return int(m.group(1))


BM_VERSION = boot_manager_version()

# What the suite flashes: the local build outputs, or with --dist DIR a `make dist` tree
FILES = {
    "pack_fs": os.path.join(REPO_ROOT, "build", "synth", "pack.fs"),
    "boot_manager": os.path.join(REPO_ROOT, "build", "firmware", "firmware.bin"),
    "zephyr_demo": os.path.join(REPO_ROOT, "build", "zephyr-demo", "zephyr", "zephyr.bin"),
    "hack_demo": os.path.join(REPO_ROOT, "build", "hack", "firmware.bin"),
}
DIST_FILES = {
    "pack_fs": "bitstream/pack.fs",
    "boot_manager": "boot-manager.bin",
    "zephyr_demo": "demos/zephyr-demo.bin",
    "hack_demo": "demos/hack-demo.bin",
}


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
    print_banner("VUX9K Real Hardware Test Suite (Tang Nano 9K + MicroSD)")
    results = []

    try:
        ser = vux_tool.open_port(port, baudrate=baud, timeout=0.1)
    except Exception as e:
        print_test_result("0. Port Connection", False, str(e))
        return False

    neighbours = usb_hub_neighbours(ser.port)
    hub_hint = ""
    if neighbours:
        hub_hint = (
            "USB devices share the board's hub (" + ", ".join(neighbours) + "); they can make the board's "
            "USB-UART bridge drop output. Give the board its own USB port or hub."
        )
        print(f"\033[93m[WARN]\033[0m {hub_hint}")

    try:
        # -------------------------------------------------------------
        # Test 1: UART Connection & Prompt Sync (vux_tool.sync_prompt)
        # -------------------------------------------------------------
        test_name = "1. UART Connection & Prompt Synchronization"
        passed, resp = vux_tool.sync_prompt(ser, timeout=4.0)
        msg = (
            "Connected and synchronized with Boot Manager"
            if passed
            else f"Failed to synchronize prompt. Output: {resp!r}"
        )
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)
        if not passed:
            print("\n[ERROR] Could not communicate with Boot Manager on Tang Nano 9K.")
            return False

        # -------------------------------------------------------------
        # Test 2: Hardware Self-Diagnostics (vux_tool.run_diag)
        # -------------------------------------------------------------
        test_name = "2. Hardware Self-Diagnostics ('t' / vux_tool.run_diag)"
        out = vux_tool.run_diag(ser, timeout=8.0)
        passed = ("[DIAG]" in out) and ("Diagnostics Complete" in out)
        msg = "Executed onboard LED, UART, and SPI diagnostics" if passed else f"Failed diagnostics. Output: {out!r}"
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)

        # -------------------------------------------------------------
        # Test 3: MicroSD Sector 0 (MBR) Dump (vux_tool.dump_mbr)
        # -------------------------------------------------------------
        test_name = "3. MicroSD Card Sector 0 (MBR) Dump ('d' / vux_tool.dump_mbr)"
        out = vux_tool.dump_mbr(ser, timeout=8.0)
        passed = ("[SD]" in out) and (("55 AA" in out) or ("55aa" in out.lower()) or ("Signature:" in out))
        msg = "Read 512-byte Sector 0 from physical MicroSD" if passed else f"Failed sector dump. Output: {out!r}"
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)

        # -------------------------------------------------------------
        # Test 4: Flash Slot 0: Boot Manager (vux_tool.flash_slot)
        # -------------------------------------------------------------
        boot_mgr_bin = FILES["boot_manager"]
        test_name_flash_s0 = "4. Flash Slot 0: Boot Manager ('w' / vux_tool.flash_slot)"
        try:
            meta = vux_tool.flash_slot(ser, boot_mgr_bin, slot=0, name="Boot Manager", mode="riscv", version=BM_VERSION)
            results.append(
                (
                    test_name_flash_s0,
                    True,
                    f"Flashed {boot_mgr_bin} to Slot 0 "
                    f"(LBA 64, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
                )
            )
            print_test_result(
                test_name_flash_s0,
                True,
                f"Flashed {boot_mgr_bin} to Slot 0 (LBA 64, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
            )
        except Exception as e:
            results.append((test_name_flash_s0, False, str(e)))
            print_test_result(test_name_flash_s0, False, str(e))

        # -------------------------------------------------------------
        # Test 5: Inspect Slot 0 Header (vux_tool.inspect_slot)
        # -------------------------------------------------------------
        test_name_insp_s0 = "5. Header Verification: Slot 0 ('s0' / vux_tool.inspect_slot)"
        out = vux_tool.inspect_slot(ser, slot=0, timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  1" in out) or ("RISC-V" in out))
        msg = (
            "Verified Slot 0 header (Magic: VUX9, Mode: 1/RISC-V, Name: Boot Manager)"
            if passed
            else f"Failed inspect. Output: {out!r}"
        )
        results.append((test_name_insp_s0, passed, msg))
        print_test_result(test_name_insp_s0, passed, msg)

        # -------------------------------------------------------------
        # Test 6: Flash Slot 1: Default RISC-V App (vux_tool.flash_slot)
        # -------------------------------------------------------------
        # The Zephyr Rust demo (make build-zephyr-demo)
        app_bin = FILES["zephyr_demo"]
        if not os.path.exists(app_bin):
            app_bin = os.path.join(REPO_ROOT, "build", "firmware", "test_payload.bin")
            os.makedirs(os.path.dirname(app_bin), exist_ok=True)
            with open(app_bin, "wb") as f:
                f.write(bytes([0x93, 0x02, 0x00, 0x00, 0x93, 0x82, 0x12, 0x00]))

        test_name_flash_s1 = "6. Flash Slot 1: Default RISC-V App ('w' / vux_tool.flash_slot)"
        try:
            meta = vux_tool.flash_slot(ser, app_bin, slot=1, name="Zephyr Rust", mode="riscv")
            results.append(
                (
                    test_name_flash_s1,
                    True,
                    f"Flashed {app_bin} to Slot 1 (LBA 128, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
                )
            )
            print_test_result(
                test_name_flash_s1,
                True,
                f"Flashed {app_bin} to Slot 1 (LBA 128, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
            )
        except Exception as e:
            results.append((test_name_flash_s1, False, str(e)))
            print_test_result(test_name_flash_s1, False, str(e))

        # -------------------------------------------------------------
        # Test 7: Inspect Slot 1 Header (vux_tool.inspect_slot)
        # -------------------------------------------------------------
        test_name_insp_s1 = "7. Header Verification: Slot 1 ('s1' / vux_tool.inspect_slot)"
        out = vux_tool.inspect_slot(ser, slot=1, timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  1" in out) or ("RISC-V" in out))
        results.append(
            (test_name_insp_s1, passed, "Verified Slot 1 header (Magic: VUX9, Mode: 1/RISC-V, Name: Rust App)")
        )
        print_test_result(
            test_name_insp_s1, passed, "Verified Slot 1 header (Magic: VUX9, Mode: 1/RISC-V, Name: Rust App)"
        )

        # -------------------------------------------------------------
        # Test 8: Flash Slot 2: Hack 16-bit Firmware (vux_tool.flash_slot)
        # -------------------------------------------------------------
        hack_bin = FILES["hack_demo"]
        if not os.path.exists(hack_bin):
            os.makedirs(os.path.join(REPO_ROOT, "build", "hack"), exist_ok=True)
            with open(hack_bin, "wb") as f:
                f.write(bytes([0x00, 0x00, 0x01, 0x00, 0x02, 0x00]))

        test_name_flash_s2 = "8. Flash Slot 2: Hack 16-bit Firmware ('w' / vux_tool.flash_slot)"
        try:
            meta = vux_tool.flash_slot(ser, hack_bin, slot=2, name="Hack Demo", mode="hack")
            results.append(
                (
                    test_name_flash_s2,
                    True,
                    f"Flashed {hack_bin} to Slot 2 (LBA 192, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
                )
            )
            print_test_result(
                test_name_flash_s2,
                True,
                f"Flashed {hack_bin} to Slot 2 (LBA 192, {meta['num_sectors']} sectors, CRC=0x{meta['crc32']:08X})",
            )
        except Exception as e:
            results.append((test_name_flash_s2, False, str(e)))
            print_test_result(test_name_flash_s2, False, str(e))

        # -------------------------------------------------------------
        # Test 9: Inspect Slot 2 Header (vux_tool.inspect_slot)
        # -------------------------------------------------------------
        test_name_insp_s2 = "9. Header Verification: Slot 2 ('s2' / vux_tool.inspect_slot)"
        out = vux_tool.inspect_slot(ser, slot=2, timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  0" in out) or ("Hack" in out))
        msg = (
            "Verified Slot 2 header (Magic: VUX9, Mode: 0/Hack, Name: Hack Demo)"
            if passed
            else f"Failed inspect. Output: {out!r}"
        )
        results.append((test_name_insp_s2, passed, msg))
        print_test_result(test_name_insp_s2, passed, msg)

        # -------------------------------------------------------------
        # Test 10: Program Slots Catalog Listing (vux_tool.list_slots)
        # -------------------------------------------------------------
        test_name_catalog = "10. Program Slots Catalog Listing ('l' / vux_tool.list_slots)"
        out = vux_tool.list_slots(ser, timeout=8.0)
        passed = ("Slot 0" in out) and ("Slot 1" in out) and ("Slot 2" in out)
        msg = (
            "Verified multi-slot catalog listing with names and sizes"
            if passed
            else f"Catalog incomplete. Output: {out!r}"
        )
        results.append((test_name_catalog, passed, msg))
        print_test_result(test_name_catalog, passed, msg)

        # -------------------------------------------------------------
        # Test 11: Negative Test: CRC32 Corrupted Payload Rejection
        # -------------------------------------------------------------
        test_name_crc = "11. Negative Test: CRC32 Corrupted Payload Rejection (Slot 0)"
        try:
            vux_tool.flash_slot(
                ser,
                boot_mgr_bin,
                slot=0,
                name="CorruptBM",
                mode="riscv",
                version=BM_VERSION + 1,
                crc_override=0xDEADBEEF,
            )
            out = vux_tool.reboot_soc(ser, timeout=8.0)
            passed = "CRC32 mismatch" in out or "Corrupted payload" in out or "invalid" in out
            if passed and "vux>" not in out:
                p_ok, _ = vux_tool.sync_prompt(ser, timeout=4.0)
                passed = passed and p_ok
            msg = (
                "Detected corrupted CRC32, bypassed auto-update, and preserved Boot Manager"
                if passed
                else f"Failed CRC check. Output: {out!r}"
            )
            # Restore Slot 0 to the Boot Manager under test
            time.sleep(0.5)
            vux_tool.sync_prompt(ser, timeout=4.0)
            vux_tool.flash_slot(ser, boot_mgr_bin, slot=0, name="Boot Manager", mode="riscv", version=BM_VERSION)
            results.append((test_name_crc, passed, msg))
            print_test_result(test_name_crc, passed, msg)
        except Exception as e:
            results.append((test_name_crc, False, str(e)))
            print_test_result(test_name_crc, False, str(e))

        # -------------------------------------------------------------
        # Test 12: Negative Test: Invalid Magic Header Rejection (Slot 3)
        # -------------------------------------------------------------
        test_name_magic = "12. Negative Test: Invalid Magic Header Rejection ('3')"
        try:
            vux_tool.flash_slot(ser, b"\x00" * 64, slot=3, name="BadMagic", mode="riscv", magic_override=0x12345678)
            out = vux_tool.boot_slot(ser, slot=3, timeout=8.0)
            passed = "Load failed" in out or "returning to Boot Manager" in out
            if passed and "vux>" not in out:
                p_ok, _ = vux_tool.sync_prompt(ser, timeout=4.0)
                passed = passed and p_ok
            msg = (
                "Resident Loader rejected invalid Magic header and returned to Boot Manager"
                if passed
                else f"Failed Magic check. Output: {out!r}"
            )
            results.append((test_name_magic, passed, msg))
            print_test_result(test_name_magic, passed, msg)
        except Exception as e:
            results.append((test_name_magic, False, str(e)))
            print_test_result(test_name_magic, False, str(e))

        # -------------------------------------------------------------
        # Test 13: Boot Manager v2 Self-Update and Rollback
        # -------------------------------------------------------------
        test_name_update = "13. Boot Manager Self-Update & Rollback"
        try:
            vux_tool.flash_slot(ser, boot_mgr_bin, slot=0, name="BootMgr v2", mode="riscv", version=BM_VERSION + 1)
            out = vux_tool.reboot_soc(ser, timeout=8.0)
            passed = (
                "Verified valid Boot Manager update" in out
                or "Booted newly updated" in out
                or f"v{BM_VERSION + 1}" in out
            ) and ("Auto-updating" in out or "Booted newly updated" in out)
            if passed and "vux>" not in out:
                p_ok, _ = vux_tool.sync_prompt(ser, timeout=4.0)
                passed = passed and p_ok
            msg = (
                f"Verified automatic Boot Manager self-update from Slot 0 (v{BM_VERSION + 1})"
                if passed
                else f"Failed self-update. Output: {out!r}"
            )

            # Restore Slot 0 to the Boot Manager under test
            time.sleep(0.5)
            vux_tool.sync_prompt(ser, timeout=4.0)
            vux_tool.flash_slot(ser, boot_mgr_bin, slot=0, name="Boot Manager", mode="riscv", version=BM_VERSION)

            results.append((test_name_update, passed, msg))
            print_test_result(test_name_update, passed, msg)
        except Exception as e:
            results.append((test_name_update, False, str(e)))
            print_test_result(test_name_update, False, str(e))

        # -------------------------------------------------------------
        # Test 14: Boot Slot 1: Zephyr Rust demo
        # -------------------------------------------------------------
        test_name_boot_s1 = "14. Boot Slot 1: Zephyr Rust App Execution ('1' / vux_tool.boot_slot)"
        out = vux_tool.boot_slot(ser, slot=1, timeout=8.0)
        passed = (
            ("[RL] Slot 1" in out)
            and ("[Rust App]" in out)
            and ("All Rust application tasks finished successfully!" in out)
        )
        msg = "Executed the Zephyr Rust application tasks" if passed else f"Failed Slot 1 execution. Output: {out!r}"
        if not passed and hub_hint:
            msg += f"\n       -> {hub_hint}"
        results.append((test_name_boot_s1, passed, msg))
        print_test_result(test_name_boot_s1, passed, msg)

        # A launched application never returns to the Boot Manager: reconfigure the FPGA to get it back
        ser.close()
        pack_fs = FILES["pack_fs"]
        loader_bin = shutil.which("openFPGALoader") or os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")
        subprocess.run(
            [loader_bin, "-b", "tangnano9k", pack_fs], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.5)
        ser = vux_tool.open_port(port, baudrate=baud, timeout=0.1)
        vux_tool.sync_prompt(ser, timeout=4.0)

        # -------------------------------------------------------------
        # Test 15: Boot Slot 2: Hack 16-bit Firmware Execution
        # -------------------------------------------------------------
        test_name_boot_s2 = "15. Boot Slot 2: Hack 16-bit Firmware Execution ('2')"
        vux_tool.drain_serial(ser, timeout=0.1)
        ser.write(b"2")
        ser.flush()
        start = time.time()
        out = ""
        while time.time() - start < 40.0:
            c = ser.read(128)
            if c:
                out += c.decode("utf-8", errors="replace")
                if "ALL HACK C FIRMWARE TESTS PASSED" in out:
                    break
        passed = (
            ("[RL] Slot 2" in out)
            and ("Hack 16-bit C Firmware Test" in out)
            and ("ALL HACK C FIRMWARE TESTS PASSED" in out)
        )
        msg = (
            "Executed Hack 16-bit C firmware and verified 100% test pass"
            if passed
            else f"Failed Slot 2 execution. Output: {out!r}"
        )
        if not passed and hub_hint:
            msg += f"\n       -> {hub_hint}"
        results.append((test_name_boot_s2, passed, msg))
        print_test_result(test_name_boot_s2, passed, msg)

        # Reconfigure the FPGA again, so the board is left at the Boot Manager
        ser.close()
        pack_fs = FILES["pack_fs"]
        loader_bin = shutil.which("openFPGALoader") or os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")
        subprocess.run(
            [loader_bin, "-b", "tangnano9k", pack_fs], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.5)
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
    parser = argparse.ArgumentParser(description="VUX9K Automated Hardware Test Suite")
    parser.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--dist", metavar="DIR", help="flash the files of a `make dist` tree instead of build/")
    args = parser.parse_args()

    if args.dist:
        for key, rel in DIST_FILES.items():
            FILES[key] = os.path.join(args.dist, rel)
            if not os.path.isfile(FILES[key]):
                sys.exit(f"{FILES[key]} missing: not a `make dist` tree?")

    success = run_hardware_test_suite(port=args.port, baud=args.baud)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
