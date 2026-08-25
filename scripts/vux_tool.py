#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Host Tooling (vux_tool.py)
Provides unified communication with VUX9K Boot Manager on Tang Nano 9K:
- Real-time Serial Terminal Monitor
- Hardware Diagnostics Trigger
- SD Card Sector 0 (MBR) Dump
- SD Card Sector 64 (Boot Sector) Flashing & Verification (Multi-Sector Support)
- SD Card Load & Boot Trigger
- Dual-ISA (Hack / RISC-V) Binary Preparation
"""

import sys
import os
import time
import struct
import argparse

try:
    import pyftdi.serialext
except ImportError:
    pyftdi = None

try:
    import serial
except ImportError:
    serial = None

VUX_MAGIC = 0x56555839  # "VUX9"
DEFAULT_FTDI_URL = "ftdi://ftdi:2232/2"


def open_port(port_name="auto", baudrate=115200, timeout=0.2):
    if port_name == "auto" or port_name.startswith("ftdi://"):
        url = DEFAULT_FTDI_URL if port_name == "auto" else port_name
        if pyftdi is not None:
            try:
                ser = pyftdi.serialext.serial_for_url(url, baudrate=baudrate, timeout=timeout, rtscts=False, dsrdtr=False)
                ser.dtr = False
                ser.rts = False
                return ser
            except Exception as e:
                if port_name != "auto":
                    raise e

        # Fallback to SIPEED /dev/serial/by-id or /dev/ttyUSB3
        candidate_ports = []
        sipeed_id = "/dev/serial/by-id/usb-SIPEED_JTAG_Debugger_FactoryAIOT_Pro-if01-port0"
        if os.path.exists(sipeed_id):
            candidate_ports.append(sipeed_id)
        candidate_ports.extend(["/dev/ttyUSB3", "/dev/ttyUSB2", "/dev/ttyUSB1", "/dev/ttyUSB0"])

        if serial is not None:
            for p in candidate_ports:
                try:
                    ser = serial.Serial(p, baudrate=baudrate, timeout=timeout, rtscts=False, dsrdtr=False)
                    ser.dtr = False
                    ser.rts = False
                    return ser
                except Exception:
                    continue
        raise RuntimeError("Could not open Tang Nano 9K UART port automatically.")
    else:
        if serial is None:
            raise RuntimeError("pyserial is not installed!")
        ser = serial.Serial(port_name, baudrate=baudrate, timeout=timeout, rtscts=False, dsrdtr=False)
        ser.dtr = False
        ser.rts = False
        return ser


def send_cmd_and_wait(ser, cmd_char, timeout=5.0):
    ser.reset_input_buffer()
    ser.write(b"\r\n")
    ser.flush()
    time.sleep(0.05)
    ser.reset_input_buffer()
    ser.write(cmd_char.encode("utf-8"))
    ser.flush()
    start = time.time()
    buf = ""
    while time.time() - start < timeout:
        c = ser.read(256)
        if c:
            text = c.decode("utf-8", errors="replace")
            buf += text
            print(text, end="", flush=True)
            if "vux> " in buf and len(buf) > 3:
                break
    return buf


def cmd_monitor(args):
    """Interactive Serial Terminal Monitor"""
    ser = open_port(args.port, baudrate=args.baud, timeout=0.1)
    print(f"=== Connected to VUX9K on {args.port} ({args.baud} bps) ===")
    print("Press Ctrl+C to exit terminal.\n")
    try:
        while True:
            data = ser.read(256)
            if data:
                print(data.decode("utf-8", errors="replace"), end="", flush=True)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n=== Disconnected ===")
    finally:
        ser.close()


def cmd_diag(args):
    """Run hardware diagnostic tests"""
    ser = open_port(args.port, baudrate=args.baud)
    send_cmd_and_wait(ser, "t", timeout=4.0)
    ser.close()


def cmd_dump_mbr(args):
    """Dump Sector 0 (MBR) from SD Card"""
    ser = open_port(args.port, baudrate=args.baud)
    send_cmd_and_wait(ser, "d", timeout=5.0)
    ser.close()


def cmd_inspect_sd(args):
    """Inspect Sector 64 (Boot Sector) Header"""
    ser = open_port(args.port, baudrate=args.baud)
    send_cmd_and_wait(ser, "s", timeout=3.0)
    ser.close()


def cmd_boot(args):
    """Trigger Load & Boot from SD Card Sector 64"""
    ser = open_port(args.port, baudrate=args.baud, timeout=0.2)
    print("=== Triggering SD Card Boot [l] ===")
    ser.reset_input_buffer()
    ser.write(b"l")
    ser.flush()
    start = time.time()
    while time.time() - start < 4.0:
        c = ser.read(256)
        if c:
            print(c.decode("utf-8", errors="replace"), end="", flush=True)
    ser.close()


def cmd_flash_sd(args):
    """Flash a binary image to SD Card Sector 64 (Multi-Sector Support)"""
    if not os.path.exists(args.file):
        print(f"Error: File {args.file} not found!")
        sys.exit(1)

    with open(args.file, "rb") as f:
        payload = f.read()

    mode_val = 0 if args.mode == "hack" else 1
    size_bytes = len(payload)

    # Header: 16 bytes (Magic, Mode, Size, Flags)
    header = struct.pack("<IIII", VUX_MAGIC, mode_val, size_bytes, 0)
    raw_data = header + payload

    # Pad to multiple of 512 bytes
    rem = len(raw_data) % 512
    if rem != 0:
        raw_data = raw_data + b"\x00" * (512 - rem)

    num_sectors = len(raw_data) // 512
    print(f"=== Preparing VUX9 Multi-Sector Boot Image ===")
    print(f"  File: {args.file} ({size_bytes} bytes payload)")
    print(f"  Mode: {args.mode.upper()} (mode={mode_val})")
    print(f"  Total Sectors: {num_sectors} ({len(raw_data)} bytes)")

    ser = open_port(args.port, baudrate=args.baud, timeout=0.1)
    ser.reset_input_buffer()

    print("=== Initiating Multi-Sector Flash to Sector 64 ===")
    
    # 1. Wait for [READY] with gentle retry
    buf = b""
    start = time.time()
    ready = False
    for attempt in range(3):
        ser.reset_input_buffer()
        ser.write(b"w")
        ser.flush()
        
        attempt_start = time.time()
        while time.time() - attempt_start < 1.5:
            c = ser.read(64)
            if c:
                buf += c
                if b"[READY]" in buf:
                    ready = True
                    break
            time.sleep(0.01)
        if ready:
            break

    if not ready:
        print(f"Error: SoC did not respond with [READY]. Output: {buf.decode('utf-8', errors='replace')}")
        ser.close()
        sys.exit(1)

    # 2. Send sector count (1 byte)
    ser.write(bytes([num_sectors]))
    ser.flush()

    # 3. Stream sectors with per-sector handshake
    for sec_idx in range(num_sectors):
        # Wait for [READY-SEC:x]
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
            print(f"Error: Timeout waiting for token {sec_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}")
            ser.close()
            sys.exit(1)

        print(f"Writing Sector {64 + sec_idx} ({sec_idx + 1}/{num_sectors})...")
        sector_bytes = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        for b in sector_bytes:
            ser.write(bytes([b]))
            ser.flush()
            time.sleep(0.001)

    # 4. Wait for [SD-OK]
    buf = b""
    start = time.time()
    while time.time() - start < 6.0:
        c = ser.read(64)
        if c:
            buf += c
            text = c.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
            if b"vux> " in buf:
                break

    print("\n=== Verifying Sector 64 Header ===")
    send_cmd_and_wait(ser, "s", timeout=2.0)
    ser.close()
    print("\n=== Multi-Sector Flash & Verification Complete! ===")


def main():
    parser = argparse.ArgumentParser(description="VUX9K Host Tooling")
    parser.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # monitor
    subparsers.add_parser("monitor", help="Open serial terminal monitor")

    # diag
    subparsers.add_parser("diag", help="Run hardware diagnostics")

    # dump-mbr
    subparsers.add_parser("dump-mbr", help="Dump SD Card Sector 0 (MBR)")

    # inspect-sd
    subparsers.add_parser("inspect-sd", help="Inspect Sector 64 Boot Header")

    # boot
    subparsers.add_parser("boot", help="Load & Boot payload from SD Sector 64")

    # flash-sd
    sub_flash = subparsers.add_parser("flash-sd", help="Flash binary to SD Card Sector 64")
    sub_flash.add_argument("file", help="Binary file to flash")
    sub_flash.add_argument("--mode", choices=["hack", "riscv"], default="hack", help="Target ISA mode")

    args = parser.parse_args()

    if args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "diag":
        cmd_diag(args)
    elif args.command == "dump-mbr":
        cmd_dump_mbr(args)
    elif args.command == "inspect-sd":
        cmd_inspect_sd(args)
    elif args.command == "boot":
        cmd_boot(args)
    elif args.command == "flash-sd":
        cmd_flash_sd(args)


if __name__ == "__main__":
    main()
