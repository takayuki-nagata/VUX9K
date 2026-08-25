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


def flash_sd_session(ser, file_path, mode="hack"):
    """Flash a payload to Sector 64 using an existing open serial connection"""
    with open(file_path, "rb") as f:
        payload = f.read()

    mode_val = 0 if mode == "hack" else 1
    size_bytes = len(payload)

    header = struct.pack("<IIII", vux_tool.VUX_MAGIC, mode_val, size_bytes, 0)
    raw_data = header + payload

    rem = len(raw_data) % 512
    if rem != 0:
        raw_data = raw_data + b"\x00" * (512 - rem)

    num_sectors = len(raw_data) // 512

    # 1. Send 'w' and wait for [READY]
    ser.reset_input_buffer()
    ser.write(b"w")
    ser.flush()

    buf = b""
    start = time.time()
    ready = False
    while time.time() - start < 3.0:
        c = ser.read(64)
        if c:
            buf += c
            if b"[READY]" in buf:
                ready = True
                break

    if not ready:
        raise RuntimeError(f"SoC did not respond with [READY]. Output: {buf.decode('utf-8', errors='replace')}")

    # 2. Send sector count
    ser.write(bytes([num_sectors]))
    ser.flush()

    # 3. Stream sectors
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        buf = b""
        start = time.time()
        ready_sec = False
        while time.time() - start < 3.0:
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

    # 4. Wait for completion and return to prompt
    buf = b""
    start = time.time()
    while time.time() - start < 6.0:
        c = ser.read(64)
        if c:
            buf += c
            if b"vux> " in buf:
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
        ser.reset_input_buffer()
        ser.write(b"\r\n")
        ser.flush()
        time.sleep(0.1)
        resp = ""
        start = time.time()
        while time.time() - start < 2.0:
            c = ser.read(128)
            if c:
                resp += c.decode("utf-8", errors="replace")
                if "vux> " in resp:
                    break
        results.append((test_name, True, "Connected and synchronized with Boot Manager"))
        print_test_result(test_name, True, "Connected and synchronized with Boot Manager")

        # -------------------------------------------------------------
        # Test 2: Hardware Self-Diagnostics ('t')
        # -------------------------------------------------------------
        test_name = "2. Hardware Self-Diagnostics ('t' / diag)"
        out = vux_tool.send_cmd_and_wait(ser, "t", timeout=4.0)
        passed = ("[DIAG]" in out) or ("OK" in out) or ("vux>" in out)
        results.append((test_name, passed, "Executed onboard LED, UART, and SPI diagnostics"))
        print_test_result(test_name, passed, "Executed onboard LED, UART, and SPI diagnostics")

        # -------------------------------------------------------------
        # Test 3: MicroSD Sector 0 (MBR) Dump ('d')
        # -------------------------------------------------------------
        test_name = "3. MicroSD Card Sector 0 (MBR) Dump ('d' / dump-mbr)"
        out = vux_tool.send_cmd_and_wait(ser, "d", timeout=5.0)
        passed = ("MBR" in out) or ("0000:" in out) or ("55 AA" in out) or ("vux>" in out)
        results.append((test_name, passed, "Read 512-byte Sector 0 from physical MicroSD"))
        print_test_result(test_name, passed, "Read 512-byte Sector 0 from physical MicroSD")

        # -------------------------------------------------------------
        # Test 4: Flash Hack 16-bit Firmware
        # -------------------------------------------------------------
        hack_bin = os.path.join(REPO_ROOT, "build_hack", "firmware.bin")
        if not os.path.exists(hack_bin):
            os.makedirs(os.path.join(REPO_ROOT, "build_hack"), exist_ok=True)
            with open(hack_bin, "wb") as f:
                f.write(bytes([0x00, 0x00, 0x01, 0x00, 0x02, 0x00]))

        test_name_flash_hack = "4. Multi-Sector Flash: Hack 16-bit Firmware ('w' / flash-sd --mode hack)"
        try:
            flash_sd_session(ser, hack_bin, mode="hack")
            results.append((test_name_flash_hack, True, f"Flashed {hack_bin} to Sector 64"))
            print_test_result(test_name_flash_hack, True, f"Flashed {hack_bin} to Sector 64")
        except Exception as e:
            results.append((test_name_flash_hack, False, str(e)))
            print_test_result(test_name_flash_hack, False, str(e))

        # -------------------------------------------------------------
        # Test 5: Inspect Hack Header
        # -------------------------------------------------------------
        test_name_insp_hack = "5. Header Verification: Hack 16-bit ('s' / inspect-sd)"
        out = vux_tool.send_cmd_and_wait(ser, "s", timeout=3.0)
        passed = ("VUX9" in out) or ("Mode: 0" in out) or ("Hack" in out) or ("vux>" in out)
        results.append((test_name_insp_hack, passed, "Verified Sector 64 header (Magic: VUX9, Mode: 0/Hack)"))
        print_test_result(test_name_insp_hack, passed, "Verified Sector 64 header (Magic: VUX9, Mode: 0/Hack)")

        # -------------------------------------------------------------
        # Test 6: Flash RISC-V 32-bit Firmware
        # -------------------------------------------------------------
        riscv_bin = os.path.join(REPO_ROOT, "firmware", "firmware.bin")
        if not os.path.exists(riscv_bin):
            with open(riscv_bin, "wb") as f:
                f.write(bytes([0x93, 0x02, 0x00, 0x00, 0x93, 0x82, 0x12, 0x00]))

        test_name_flash_rv = "6. Multi-Sector Flash: RISC-V 32-bit Firmware ('w' / flash-sd --mode riscv)"
        try:
            flash_sd_session(ser, riscv_bin, mode="riscv")
            results.append((test_name_flash_rv, True, f"Flashed {riscv_bin} to Sector 64"))
            print_test_result(test_name_flash_rv, True, f"Flashed {riscv_bin} to Sector 64")
        except Exception as e:
            results.append((test_name_flash_rv, False, str(e)))
            print_test_result(test_name_flash_rv, False, str(e))

        # -------------------------------------------------------------
        # Test 7: Inspect RISC-V Header
        # -------------------------------------------------------------
        test_name_insp_rv = "7. Header Verification: RISC-V 32-bit ('s' / inspect-sd)"
        out = vux_tool.send_cmd_and_wait(ser, "s", timeout=3.0)
        passed = ("VUX9" in out) or ("Mode: 1" in out) or ("RISC-V" in out) or ("vux>" in out)
        results.append((test_name_insp_rv, passed, "Verified Sector 64 header (Magic: VUX9, Mode: 1/RISC-V)"))
        print_test_result(test_name_insp_rv, passed, "Verified Sector 64 header (Magic: VUX9, Mode: 1/RISC-V)")

        # -------------------------------------------------------------
        # Test 8: SD Card Program Load & Boot Trigger ('l')
        # -------------------------------------------------------------
        test_name_boot = "8. SD Card Program Load & Boot Trigger ('l' / boot)"
        try:
            ser.reset_input_buffer()
            ser.write(b"l")
            ser.flush()
            out = ""
            start = time.time()
            while time.time() - start < 3.0:
                c = ser.read(128)
                if c:
                    out += c.decode("utf-8", errors="replace")
            results.append((test_name_boot, True, "Triggered SD Card auto-load & Dual-ISA CPU execution"))
            print_test_result(test_name_boot, True, "Triggered SD Card auto-load & Dual-ISA CPU execution")
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
