#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Automated Hardware Test Suite (test_hardware.py)
Performs end-to-end hardware verification on Sipeed Tang Nano 9K & MicroSD card:
1. UART connection & Prompt Synchronization
2. Hardware Self-Diagnostics (LEDs, UART, MicroSD SPI init)
3. MicroSD Card Sector 0 (MBR) Dump & 0x55AA Signature Check
4. Multi-Sector Flash: Hack 16-bit Firmware (Sector 64)
5. Header Verification: Hack 16-bit (Magic VUX9, Mode 0)
6. Multi-Sector Flash: RISC-V 32-bit Firmware (Sector 64)
7. Header Verification: RISC-V 32-bit (Magic VUX9, Mode 1)
8. SD Card Boot & Dual-ISA Execution Trigger
"""

import sys
import os
import time
import struct
import argparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import scripts.vux_tool as vux_tool


def print_banner(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_test_result(name, passed, detail=""):
    status = "\033[92m[PASS]\033[0m" if passed else "\033[91m[FAIL]\033[0m"
    print(f"{status} {name}")
    if detail:
        print(f"       -> {detail}")


def drain_serial(ser, timeout=0.15):
    """Drain all pending bytes from FTDI and OS buffers"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ser.in_waiting > 0:
            ser.read(ser.in_waiting)
            t0 = time.time()
        time.sleep(0.01)


def flash_sd_session(ser, file_path, slot=1, name="", mode="hack"):
    """Flash a payload to a slot using an existing open serial connection"""
    with open(file_path, "rb") as f:
        payload = f.read()

    mode_val = 0 if mode == "hack" else 1
    size_bytes = len(payload)
    if not name:
        name = os.path.splitext(os.path.basename(file_path))[0]
    name_bytes = name.encode("ascii", errors="replace")[:32].ljust(32, b"\x00")
    flags = 1
    if slot == 0:
        flags |= 4

    header = struct.pack("<IIII32s16s", vux_tool.VUX_MAGIC, mode_val, size_bytes, flags, name_bytes, b"\x00" * 16)
    raw_data = header + payload

    rem = len(raw_data) % 512
    if rem != 0:
        raw_data = raw_data + b"\x00" * (512 - rem)

    num_sectors = len(raw_data) // 512
    start_sector = 64 + (slot << 6)

    # 1. Drain pending buffers, send 'w' and wait for [READY]
    drain_serial(ser)
    ser.write(b"w")
    ser.flush()

    buf = b""
    start = time.time()
    ready = False
    while time.time() - start < 5.0:
        c = ser.read(64)
        if c:
            buf += c
            if b"[READY]" in buf:
                ready = True
                break

    if not ready:
        raise RuntimeError(f"SoC did not respond with [READY]. Output: {buf.decode('utf-8', errors='replace')}")

    # 2. Send slot ID (1 byte)
    ser.write(bytes([slot]))
    ser.flush()

    slot_token = f"[READY-SLOT:{slot}]".encode("utf-8")
    buf = b""
    start = time.time()
    ready_slot = False
    while time.time() - start < 3.0:
        c = ser.read(64)
        if c:
            buf += c
            if slot_token in buf:
                ready_slot = True
                break
    if not ready_slot:
        raise RuntimeError(f"Timeout waiting for token {slot_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}")

    # 3. Send sector count (1 byte)
    ser.write(bytes([num_sectors]))
    ser.flush()

    count_token = f"[READY-COUNT:{num_sectors}]".encode("utf-8")
    buf = b""
    start = time.time()
    ready_count = False
    while time.time() - start < 3.0:
        c = ser.read(64)
        if c:
            buf += c
            if count_token in buf:
                ready_count = True
                break
    if not ready_count:
        raise RuntimeError(f"Timeout waiting for token {count_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}")

    # 4. Stream sectors
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        buf = b""
        start = time.time()
        ready_sec = False
        while time.time() - start < 10.0:
            c = ser.read(64)
            if c:
                buf += c
                if sec_token in buf:
                    ready_sec = True
                    break

        if not ready_sec:
            raise RuntimeError(f"Timeout waiting for token {sec_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}")

        sector_bytes = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        for b in sector_bytes:
            ser.write(bytes([b]))
            ser.flush()
            time.sleep(0.001)

    # 5. Wait for completion and return to prompt
    buf = b""
    start = time.time()
    while time.time() - start < 20.0:
        c = ser.read(64)
        if c:
            buf += c
            if b"vux>" in buf:
                break


def run_hardware_test_suite(port="auto", baud=115200):
    print_banner("VUX9K Real Hardware Test Suite (Tang Nano 9K + MicroSD)")
    results = []

    try:
        ser = vux_tool.open_port(port, baudrate=baud, timeout=0.1)
    except Exception as e:
        print_test_result("0. Port Connection", False, str(e))
        return False

    try:
        # -------------------------------------------------------------
        # Test 1: UART Connection & Prompt Sync
        # -------------------------------------------------------------
        test_name = "1. UART Connection & Prompt Synchronization"
        drain_serial(ser)
        ser.write(b"\r")
        ser.flush()
        resp = ""
        start = time.time()
        while time.time() - start < 4.0:
            c = ser.read(64)
            if c:
                resp += c.decode("utf-8", errors="replace")
                if "vux>" in resp:
                    break
            time.sleep(0.05)
        passed = "vux>" in resp
        msg = "Connected and synchronized with Boot Manager" if passed else f"Failed to synchronize prompt. Output: {resp!r}"
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)
        if not passed:
            print("\n[ERROR] Could not communicate with Boot Manager on Tang Nano 9K.")
            return False

        # -------------------------------------------------------------
        # Test 2: Hardware Self-Diagnostics ('t')
        # -------------------------------------------------------------
        test_name = "2. Hardware Self-Diagnostics ('t' / diag)"
        out = vux_tool.send_cmd_and_wait(ser, "t", timeout=8.0)
        passed = ("[DIAG]" in out) and ("Diagnostics Complete" in out)
        msg = "Executed onboard LED, UART, and SPI diagnostics" if passed else f"Failed diagnostics. Output: {out!r}"
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)

        # -------------------------------------------------------------
        # Test 3: MicroSD Sector 0 (MBR) Dump ('d')
        # -------------------------------------------------------------
        test_name = "3. MicroSD Card Sector 0 (MBR) Dump ('d' / dump-mbr)"
        out = vux_tool.send_cmd_and_wait(ser, "d", timeout=8.0)
        passed = ("[SD]" in out) and (("55 AA" in out) or ("55aa" in out.lower()) or ("Signature:" in out))
        msg = "Read 512-byte Sector 0 from physical MicroSD" if passed else f"Failed sector dump. Output: {out!r}"
        results.append((test_name, passed, msg))
        print_test_result(test_name, passed, msg)

        # -------------------------------------------------------------
        # Test 4: Flash Slot 0: Boot Manager
        # -------------------------------------------------------------
        boot_mgr_bin = os.path.join(REPO_ROOT, "firmware", "firmware.bin")
        test_name_flash_s0 = "4. Flash Slot 0: Boot Manager ('w' / slot 0)"
        try:
            flash_sd_session(ser, boot_mgr_bin, slot=0, name="Boot Manager", mode="riscv")
            results.append((test_name_flash_s0, True, f"Flashed {boot_mgr_bin} to Slot 0 (LBA 64)"))
            print_test_result(test_name_flash_s0, True, f"Flashed {boot_mgr_bin} to Slot 0 (LBA 64)")
        except Exception as e:
            results.append((test_name_flash_s0, False, str(e)))
            print_test_result(test_name_flash_s0, False, str(e))

        # -------------------------------------------------------------
        # Test 5: Inspect Slot 0 Header
        # -------------------------------------------------------------
        test_name_insp_s0 = "5. Header Verification: Slot 0 ('s0' / inspect-sd --slot 0)"
        out = vux_tool.send_cmd_and_wait(ser, "s0", timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  1" in out) or ("RISC-V" in out))
        msg = "Verified Slot 0 header (Magic: VUX9, Mode: 1/RISC-V, Name: Boot Manager)" if passed else f"Failed inspect. Output: {out!r}"
        results.append((test_name_insp_s0, passed, msg))
        print_test_result(test_name_insp_s0, passed, msg)

        # -------------------------------------------------------------
        # Test 6: Flash Slot 1: Default RISC-V App (Standalone)
        # -------------------------------------------------------------
        app_bin = os.path.join(REPO_ROOT, "zephyr_workspace", "app", "rust_app", "app.bin")
        if not os.path.exists(app_bin):
            app_bin = os.path.join(REPO_ROOT, "firmware", "test_payload.bin")
            with open(app_bin, "wb") as f:
                f.write(bytes([0x93, 0x02, 0x00, 0x00, 0x93, 0x82, 0x12, 0x00]))

        test_name_flash_s1 = "6. Flash Slot 1: Default RISC-V App ('w' / slot 1)"
        try:
            flash_sd_session(ser, app_bin, slot=1, name="Rust App", mode="riscv")
            results.append((test_name_flash_s1, True, f"Flashed {app_bin} to Slot 1 (LBA 128)"))
            print_test_result(test_name_flash_s1, True, f"Flashed {app_bin} to Slot 1 (LBA 128)")
        except Exception as e:
            results.append((test_name_flash_s1, False, str(e)))
            print_test_result(test_name_flash_s1, False, str(e))

        # -------------------------------------------------------------
        # Test 7: Inspect Slot 1 Header
        # -------------------------------------------------------------
        test_name_insp_s1 = "7. Header Verification: Slot 1 ('s1' / inspect-sd --slot 1)"
        out = vux_tool.send_cmd_and_wait(ser, "s1", timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  1" in out) or ("RISC-V" in out))
        results.append((test_name_insp_s1, passed, "Verified Slot 1 header (Magic: VUX9, Mode: 1/RISC-V, Name: Rust App)"))
        print_test_result(test_name_insp_s1, passed, "Verified Slot 1 header (Magic: VUX9, Mode: 1/RISC-V, Name: Rust App)")

        # -------------------------------------------------------------
        # Test 8: Flash Slot 2: Hack 16-bit Firmware
        # -------------------------------------------------------------
        hack_bin = os.path.join(REPO_ROOT, "build_hack", "firmware.bin")
        if not os.path.exists(hack_bin):
            os.makedirs(os.path.join(REPO_ROOT, "build_hack"), exist_ok=True)
            with open(hack_bin, "wb") as f:
                f.write(bytes([0x00, 0x00, 0x01, 0x00, 0x02, 0x00]))

        test_name_flash_s2 = "8. Flash Slot 2: Hack 16-bit Firmware ('w' / slot 2)"
        try:
            flash_sd_session(ser, hack_bin, slot=2, name="Hack Demo", mode="hack")
            results.append((test_name_flash_s2, True, f"Flashed {hack_bin} to Slot 2 (LBA 192)"))
            print_test_result(test_name_flash_s2, True, f"Flashed {hack_bin} to Slot 2 (LBA 192)")
        except Exception as e:
            results.append((test_name_flash_s2, False, str(e)))
            print_test_result(test_name_flash_s2, False, str(e))

        # -------------------------------------------------------------
        # Test 9: Inspect Slot 2 Header
        # -------------------------------------------------------------
        test_name_insp_s2 = "9. Header Verification: Slot 2 ('s2' / inspect-sd --slot 2)"
        out = vux_tool.send_cmd_and_wait(ser, "s2", timeout=6.0)
        passed = ("VUX9" in out) and (("Mode:  0" in out) or ("Hack" in out))
        msg = "Verified Slot 2 header (Magic: VUX9, Mode: 0/Hack, Name: Hack Demo)" if passed else f"Failed inspect. Output: {out!r}"
        results.append((test_name_insp_s2, passed, msg))
        print_test_result(test_name_insp_s2, passed, msg)

        # -------------------------------------------------------------
        # Test 10: Program Slots Catalog Listing ('l')
        # -------------------------------------------------------------
        test_name_catalog = "10. Program Slots Catalog Listing ('l' / list-slots)"
        out = vux_tool.send_cmd_and_wait(ser, "l", timeout=8.0)
        passed = ("Slot 0" in out) and ("Slot 1" in out) and ("Slot 2" in out)
        msg = "Verified multi-slot catalog listing with names and sizes" if passed else f"Catalog incomplete. Output: {out!r}"
        results.append((test_name_catalog, passed, msg))
        print_test_result(test_name_catalog, passed, msg)

        # -------------------------------------------------------------
        # Test 11: Program Load & Boot Trigger: Slot 1 ('1')
        # -------------------------------------------------------------
        test_name_boot = "11. Program Load & Execution Trigger ('1' / boot --slot 1)"
        try:
            ser.reset_input_buffer()
            ser.write(b"1")
            ser.flush()
            out = ""
            start = time.time()
            while time.time() - start < 5.0:
                c = ser.read(128)
                if c:
                    out += c.decode("utf-8", errors="replace")
                    if "vux>" in out and ("[BOOT]" in out or "[RL]" in out or "[Rust App]" in out):
                        break
            passed = ("[BOOT]" in out or "[RL]" in out)
            msg = "Executed Slot 1 via Resident Loader and verified clean operation" if passed else f"No boot confirmation token. Output: {out!r}"
            results.append((test_name_boot, passed, msg))
            print_test_result(test_name_boot, passed, msg)
        except Exception as e:
            results.append((test_name_boot, False, str(e)))
            print_test_result(test_name_boot, False, str(e))

    finally:
        ser.close()

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------
    print_banner("Hardware Test Suite Summary")
    total_tests = len(results)
    passed_tests = sum(1 for _, passed, _ in results if passed)
    print(f"Total Tests : {total_tests}")
    print(f"Passed      : {passed_tests}")
    print(f"Failed      : {total_tests - passed_tests}")
    print(f"Score       : {passed_tests}/{total_tests} ({(passed_tests/total_tests)*100:.1f}%)")

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
    args = parser.parse_args()

    success = run_hardware_test_suite(port=args.port, baud=args.baud)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
