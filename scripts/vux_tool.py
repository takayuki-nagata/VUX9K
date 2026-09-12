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
import binascii

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


def find_tangnano_uart_port():
    by_id_dir = "/dev/serial/by-id"
    if os.path.exists(by_id_dir):
        try:
            entries = os.listdir(by_id_dir)
            # Priority 1: SIPEED / Debugger interface 01 (UART port)
            for entry in entries:
                entry_lower = entry.lower()
                if ("sipeed" in entry_lower or "debugger" in entry_lower or "tang" in entry_lower or "gowin" in entry_lower) and ("if01" in entry_lower or "if1" in entry_lower):
                    return os.path.join(by_id_dir, entry)
            # Priority 2: Any interface 01 device that is NOT FT232R (excluding other host FT232R UARTs)
            for entry in entries:
                entry_lower = entry.lower()
                if "ft232r" not in entry_lower and ("if01" in entry_lower or "if1" in entry_lower):
                    return os.path.join(by_id_dir, entry)
        except Exception:
            pass

    # Priority 3: /dev/ttyUSB3 (standard for Tang Nano 9K when ttyUSB2 is JTAG and ttyUSB0/1 are other hosts)
    if os.path.exists("/dev/ttyUSB3"):
        return "/dev/ttyUSB3"
    return None


def open_port(port_name="auto", baudrate=115200, timeout=0.2):
    if port_name == "auto":
        port = find_tangnano_uart_port()
        if port is not None and serial is not None:
            try:
                ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
                return ser
            except Exception as e:
                pass

        # If pyftdi is available and by-id did not work, try pyftdi URL
        if pyftdi is not None:
            try:
                ser = pyftdi.serialext.serial_for_url(DEFAULT_FTDI_URL, baudrate=baudrate, timeout=timeout)
                return ser
            except Exception:
                pass

        raise RuntimeError("Could not open Tang Nano 9K UART port automatically. Ensure Tang Nano 9K is connected.")
    elif port_name.startswith("ftdi://"):
        if pyftdi is None:
            raise RuntimeError("pyftdi is not installed for ftdi:// URLs!")
        ser = pyftdi.serialext.serial_for_url(port_name, baudrate=baudrate, timeout=timeout)
        return ser
    else:
        if serial is None:
            raise RuntimeError("pyserial is not installed!")
        ser = serial.Serial(port_name, baudrate=baudrate, timeout=timeout)
        return ser


def drain_serial(ser, timeout=0.15):
    """Drain all pending bytes from UART input buffers"""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        if getattr(ser, "in_waiting", 0) > 0:
            ser.read(ser.in_waiting)
            t0 = time.time()
        time.sleep(0.01)


def send_cmd_and_wait(ser, cmd_char, timeout=5.0):
    drain_serial(ser, timeout=0.1)
    ser.write(cmd_char.encode("utf-8"))
    ser.flush()
    start = time.time()
    buf = ""
    while time.time() - start < timeout:
        c = ser.read(256)
        if c:
            text = c.decode("utf-8", errors="replace")
            buf += text
            # Ensure prompt is checked only after command echo
            if (cmd_char in buf) and ("vux>" in buf[buf.find(cmd_char) + len(cmd_char):]):
                break
    return buf


def cmd_monitor(args):
    """Interactive Full-Duplex Serial Terminal Monitor"""
    ser = open_port(args.port, baudrate=args.baud, timeout=0.05)
    # Drain any stale or truncated bytes from prior disconnects
    drain_serial(ser, timeout=0.1)

    print(f"=== Connected to VUX9K on {args.port} ({args.baud} bps) ===")
    print("Interactive mode active: Type commands directly. Press Ctrl+C or Ctrl+] to exit.\n")

    # Send return to trigger fresh prompt
    ser.write(b"\r")
    ser.flush()

    import select
    import termios
    import tty

    is_tty = sys.stdin.isatty()
    old_settings = None
    if is_tty:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
        # Re-enable output post-processing on the terminal so bare \n triggers CR+LF
        try:
            mode = termios.tcgetattr(sys.stdin)
            mode[1] |= (termios.OPOST | termios.ONLCR)
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, mode)
        except Exception:
            pass

    try:
        while True:
            rlist = []
            if ser.fileno() >= 0:
                rlist.append(ser.fileno())
            if is_tty:
                rlist.append(sys.stdin.fileno())

            readable, _, _ = select.select(rlist, [], [], 0.05)

            if is_tty and sys.stdin.fileno() in readable:
                key = os.read(sys.stdin.fileno(), 1024)
                if not key:
                    break
                # Check for exit hotkeys (Ctrl+C: 0x03, Ctrl+]: 0x1D)
                if b"\x03" in key or b"\x1d" in key:
                    break
                ser.write(key)
                ser.flush()

            if (ser.fileno() in readable) or (getattr(ser, "in_waiting", 0) > 0):
                data = ser.read(getattr(ser, "in_waiting", 0) or 256)
                if data:
                    # Normalize all bare LFs to CRLF to avoid terminal staircase effect
                    formatted = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                    if is_tty:
                        os.write(sys.stdout.fileno(), formatted)
                    else:
                        sys.stdout.write(formatted.decode("utf-8", errors="replace"))
                        sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        if is_tty and old_settings is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print("\r\n=== Disconnected ===")
        ser.close()


# =====================================================================
# Core Reusable API Functions (Shared with test_hardware.py & tests)
# =====================================================================

def sync_prompt(ser, timeout=4.0):
    """Send return and wait for prompt synchronization ('vux>')"""
    drain_serial(ser, timeout=0.15)
    ser.write(b"\r")
    ser.flush()
    resp = ""
    start = time.time()
    while time.time() - start < timeout:
        c = ser.read(64)
        if c:
            resp += c.decode("utf-8", errors="replace")
            if "vux>" in resp:
                return True, resp
        time.sleep(0.05)
    return False, resp


def run_diag(ser, timeout=8.0):
    """Run hardware diagnostic tests ('t')"""
    return send_cmd_and_wait(ser, "t", timeout=timeout)


def dump_mbr(ser, timeout=10.0):
    """Dump Sector 0 (MBR) from SD Card ('d')"""
    return send_cmd_and_wait(ser, "d", timeout=timeout)


def inspect_slot(ser, slot=0, timeout=6.0):
    """Inspect SD Slot Boot Header (s0-s9)"""
    return send_cmd_and_wait(ser, f"s{slot}", timeout=timeout)


def list_slots(ser, timeout=8.0):
    """List Program Slots Catalog ('l')"""
    return send_cmd_and_wait(ser, "l", timeout=timeout)


def boot_slot(ser, slot=1, timeout=5.0):
    """Launch Program in Slot 1-9 ('1'-'9')"""
    drain_serial(ser, timeout=0.1)
    ser.write(str(slot).encode("ascii"))
    ser.flush()
    start = time.time()
    out = ""
    while time.time() - start < timeout:
        c = ser.read(128)
        if c:
            text = c.decode("utf-8", errors="replace")
            out += text
            if "vux>" in out and ("[BOOT]" in out or "[RL]" in out or "[Rust App]" in out):
                break
    return out


def reboot_soc(ser, timeout=8.0):
    """Reboot SoC via Resident Loader ('r')"""
    drain_serial(ser, timeout=0.1)
    ser.write(b"r")
    ser.flush()
    start = time.time()
    out = ""
    while time.time() - start < timeout:
        c = ser.read(128)
        if c:
            text = c.decode("utf-8", errors="replace")
            out += text
            if "vux>" in out and ("[RESET]" in out or "[BOOT]" in out or "[UPDATE]" in out or "Boot Manager" in out):
                break
    return out


def build_vux9_image(file_or_bytes, slot=1, name="", mode="hack", version=1, crc_override=None, magic_override=None):
    """Pack binary payload into VUX9 v2 image with 64-byte boot header and 512-byte sector padding"""
    if isinstance(file_or_bytes, (bytes, bytearray)):
        payload = bytes(file_or_bytes)
        default_name = f"slot{slot}"
    else:
        if not os.path.exists(file_or_bytes):
            raise FileNotFoundError(f"File {file_or_bytes} not found!")
        with open(file_or_bytes, "rb") as f:
            payload = f.read()
        default_name = os.path.splitext(os.path.basename(file_or_bytes))[0]

    mode_val = 0 if mode == "hack" else 1
    size_bytes = len(payload)
    if not name:
        name = default_name
    name_bytes = name.encode("ascii", errors="replace")[:32].ljust(32, b"\x00")
    flags = 1  # valid
    if slot == 0:
        flags |= 4  # system slot

    magic_val = magic_override if magic_override is not None else VUX_MAGIC
    crc_val = crc_override if crc_override is not None else (binascii.crc32(payload) & 0xFFFFFFFF)
    header = struct.pack("<IIII32sII8s", magic_val, mode_val, size_bytes, flags, name_bytes, crc_val, version, b"\x00" * 8)
    raw_data = header + payload

    rem = len(raw_data) % 512
    if rem != 0:
        raw_data = raw_data + b"\x00" * (512 - rem)

    num_sectors = len(raw_data) // 512
    if num_sectors > 64:
        raise ValueError(f"Binary with header ({num_sectors} sectors) exceeds 32KB slot limit (64 sectors)!")

    start_sector = 64 + (slot << 6)
    meta = {
        "slot": slot,
        "name": name,
        "mode": mode,
        "mode_val": mode_val,
        "size_bytes": size_bytes,
        "crc32": crc_val,
        "version": version,
        "start_sector": start_sector,
        "num_sectors": num_sectors,
        "total_bytes": len(raw_data),
    }
    return raw_data, meta


def flash_slot(ser, file_or_bytes, slot=1, name="", mode="hack", version=1, progress_cb=None, crc_override=None, magic_override=None):
    """Flash a binary image or byte payload to SD Card Slot 0-9 using an open serial port"""
    raw_data, meta = build_vux9_image(file_or_bytes, slot=slot, name=name, mode=mode, version=version, crc_override=crc_override, magic_override=magic_override)
    num_sectors = meta["num_sectors"]
    start_sector = meta["start_sector"]

    # 1. Wait for [READY] with gentle retry
    buf = b""
    ready = False
    for attempt in range(3):
        drain_serial(ser, timeout=0.1)
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
        raise RuntimeError(f"SoC did not respond with [READY]. Output: {buf.decode('utf-8', errors='replace')}")

    buf = b""
    # 2. Send slot ID (1 byte)
    ser.write(bytes([slot]))
    ser.flush()

    slot_token = f"[READY-SLOT:{slot}]".encode("utf-8")
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

    buf = b""
    # 3. Send sector count (1 byte)
    ser.write(bytes([num_sectors]))
    ser.flush()

    count_token = f"[READY-COUNT:{num_sectors}]".encode("utf-8")
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

    # 4. Stream sectors with per-sector handshake
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        start = time.time()
        ready_sec = False
        while time.time() - start < 10.0:
            if sec_token in buf:
                buf = buf[buf.find(sec_token) + len(sec_token):]
                ready_sec = True
                break
            c = ser.read(64)
            if c:
                buf += c
                if sec_token in buf:
                    buf = buf[buf.find(sec_token) + len(sec_token):]
                    ready_sec = True
                    break

        if not ready_sec:
            raise RuntimeError(f"Timeout waiting for token {sec_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}")

        if progress_cb:
            progress_cb(sec_idx, num_sectors, start_sector + sec_idx)

        sector_bytes = raw_data[sec_idx * 512 : (sec_idx + 1) * 512]
        # Send in 32-byte chunks with 4ms pacing to match 32-entry HW RX FIFO
        for offset in range(0, 512, 32):
            ser.write(sector_bytes[offset : offset + 32])
            time.sleep(0.004)
        ser.flush()

    # 5. Wait for completion and return to prompt
    buf = b""
    start = time.time()
    while time.time() - start < 6.0:
        c = ser.read(64)
        if c:
            buf += c
            if b"vux>" in buf:
                break

    meta["completion_output"] = buf.decode("utf-8", errors="replace")
    return meta


# =====================================================================
# CLI Command Wrappers
# =====================================================================

def cmd_diag(args):
    """Run hardware diagnostic tests"""
    ser = open_port(args.port, baudrate=args.baud)
    try:
        out = run_diag(ser)
        print(out)
    finally:
        ser.close()


def cmd_dump_mbr(args):
    """Dump Sector 0 (MBR) from SD Card"""
    ser = open_port(args.port, baudrate=args.baud)
    try:
        out = dump_mbr(ser)
        print(out)
    finally:
        ser.close()


def cmd_inspect_sd(args):
    """Inspect SD Slot Boot Header (Slots 0-9)"""
    ser = open_port(args.port, baudrate=args.baud)
    try:
        slot = getattr(args, "slot", 0)
        out = inspect_slot(ser, slot=slot)
        print(out)
    finally:
        ser.close()


def cmd_list_slots(args):
    """List Program Slots Catalog (Slots 0-9)"""
    ser = open_port(args.port, baudrate=args.baud)
    try:
        out = list_slots(ser)
        print(out)
    finally:
        ser.close()


def cmd_boot(args):
    """Launch Program in Slot 1-9"""
    slot = getattr(args, "slot", 1)
    ser = open_port(args.port, baudrate=args.baud, timeout=0.2)
    try:
        print(f"=== Launching Slot {slot} via Boot Manager [{slot}] ===")
        out = boot_slot(ser, slot=slot)
        print(out)
    finally:
        ser.close()


def cmd_flash_sd(args):
    """Flash a binary image to SD Card Slot 0-9 (Multi-Sector Support)"""
    if not os.path.exists(args.file):
        print(f"Error: File {args.file} not found!")
        sys.exit(1)

    slot = getattr(args, "slot", 1)
    name = getattr(args, "name", "")
    mode = getattr(args, "mode", "hack")
    version = getattr(args, "version", 1)

    def progress(idx, total, sec):
        print(f"Writing Sector {sec} ({idx + 1}/{total})...")

    ser = open_port(args.port, baudrate=args.baud, timeout=0.1)
    try:
        _, meta = build_vux9_image(args.file, slot=slot, name=name, mode=mode, version=version)
        print(f"=== Preparing VUX9 v2 Slot Boot Image ===")
        print(f"  File: {args.file} ({meta['size_bytes']} bytes payload)")
        print(f"  Slot: {slot} (Sector {meta['start_sector']}, LBA {meta['start_sector']})")
        print(f"  Name: {meta['name']!r}")
        print(f"  Mode: {meta['mode'].upper()} (mode={meta['mode_val']})")
        print(f"  Version: {meta['version']}")
        print(f"  CRC32: 0x{meta['crc32']:08X}")
        print(f"  Total Sectors: {meta['num_sectors']} ({meta['total_bytes']} bytes)")

        print(f"=== Initiating Flash to Slot {slot} (Sector {meta['start_sector']}) ===")
        res = flash_slot(ser, args.file, slot=slot, name=name, mode=mode, version=version, progress_cb=progress)
        if "completion_output" in res:
            print(res["completion_output"], end="", flush=True)

        print(f"\n=== Verifying Slot {slot} Header ===")
        out = inspect_slot(ser, slot=slot)
        print(out)
        print(f"\n=== Multi-Sector Flash & Verification Complete! ===")
    except Exception as e:
        print(f"Error flashing slot: {e}")
        sys.exit(1)
    finally:
        ser.close()


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

    # list-slots
    subparsers.add_parser("list-slots", help="List Program Slots Catalog (Slots 0-9)")

    # inspect-sd
    sub_insp = subparsers.add_parser("inspect-sd", help="Inspect SD Slot Boot Header (Slots 0-9)")
    sub_insp.add_argument("--slot", type=int, default=0, choices=range(0, 10), help="Slot number (0-9, default: 0)")

    # boot
    sub_boot = subparsers.add_parser("boot", help="Launch program in Slot 1-9")
    sub_boot.add_argument("--slot", type=int, default=1, choices=range(1, 10), help="Slot number (1-9, default: 1)")

    # flash-sd
    sub_flash = subparsers.add_parser("flash-sd", help="Flash binary to SD Card Slot 0-9")
    sub_flash.add_argument("file", help="Binary file to flash")
    sub_flash.add_argument("--slot", type=int, default=1, choices=range(0, 10), help="Slot number (0-9, default: 1)")
    sub_flash.add_argument("--name", default="", help="Program name (max 32 characters)")
    sub_flash.add_argument("--mode", choices=["hack", "riscv"], default="hack", help="Target ISA mode")
    sub_flash.add_argument("--version", type=int, default=1, help="Version number (default: 1)")

    args = parser.parse_args()

    if args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "diag":
        cmd_diag(args)
    elif args.command == "dump-mbr":
        cmd_dump_mbr(args)
    elif args.command == "list-slots":
        cmd_list_slots(args)
    elif args.command == "inspect-sd":
        cmd_inspect_sd(args)
    elif args.command == "boot":
        cmd_boot(args)
    elif args.command == "flash-sd":
        cmd_flash_sd(args)


if __name__ == "__main__":
    main()
