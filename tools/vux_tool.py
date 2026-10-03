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

import argparse
import binascii
import os
import struct
import sys
import time
from dataclasses import dataclass

try:
    import pyftdi.serialext
except ImportError:
    pyftdi = None

try:
    import serial
except ImportError:
    serial = None

# The VUX9 v3 slot header, as firmware/fw_common/src/header.rs (the firmware's side)
# defines it; sim/emu/test_vux9_header.py keeps the two in step.
VUX_MAGIC = 0x56555839  # "VUX9"
HEADER_VERSION = 3
HEADER_LEN = 64
SECTOR = 512
FLAG_VALID = 1
FLAG_SYSTEM = 4  # slot 0, the Boot Manager's update image
MODE_HACK = 0
MODE_RISCV = 1
# magic, header_version (u16), flags (u16), mode, size, load_addr, entry_point, crc32,
# version (the application's), name (32 bytes, NUL-padded); little-endian
HEADER_FORMAT = "<IHHIIIIII32s"
SLOT_SECTORS = 64  # 32 KB per slot


def slot_sector(slot):
    """First SD sector (LBA) of slot 0-9."""
    return 64 + slot * SLOT_SECTORS


@dataclass(frozen=True)
class SlotHeader:
    mode: int
    size: int
    crc32: int
    name: bytes = b""
    version: int = 1
    load_addr: int = 0
    entry_point: int = 0
    flags: int = FLAG_VALID
    header_version: int = HEADER_VERSION

    def pack(self, magic=VUX_MAGIC):
        return struct.pack(
            HEADER_FORMAT,
            magic,
            self.header_version,
            self.flags,
            self.mode,
            self.size,
            self.load_addr,
            self.entry_point,
            self.crc32,
            self.version,
            self.name[:32].ljust(32, b"\x00"),
        )

    @classmethod
    def parse(cls, sector):
        """The header at the start of sector if the firmware accepts it (magic, version,
        valid flag; fw_common's SlotHeader::parse), else None."""
        if len(sector) < HEADER_LEN:
            return None
        magic, hver, flags, mode, size, load, entry, crc, ver, name = struct.unpack_from(HEADER_FORMAT, sector)
        if magic != VUX_MAGIC or hver != HEADER_VERSION or not flags & FLAG_VALID:
            return None
        return cls(mode, size, crc, name, ver, load, entry, flags, hver)

    def name_str(self):
        """The printable (0x20-0x7E) prefix of the name, as the Boot Manager shows it."""
        n = next((i for i, c in enumerate(self.name) if not 0x20 <= c <= 0x7E), len(self.name))
        return self.name[:n].decode("ascii")


assert struct.calcsize(HEADER_FORMAT) == HEADER_LEN
DEFAULT_FTDI_URL = "ftdi://ftdi:2232/2"


def find_tangnano_uart_port():
    by_id_dir = "/dev/serial/by-id"
    if os.path.exists(by_id_dir):
        try:
            entries = os.listdir(by_id_dir)
            # Priority 1: SIPEED / Debugger interface 01 (UART port)
            for entry in entries:
                entry_lower = entry.lower()
                if (
                    "sipeed" in entry_lower
                    or "debugger" in entry_lower
                    or "tang" in entry_lower
                    or "gowin" in entry_lower
                ) and ("if01" in entry_lower or "if1" in entry_lower):
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
            except Exception:
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
        # serial_for_url also takes URLs such as socket://localhost:PORT (the emulator)
        ser = serial.serial_for_url(port_name, baudrate=baudrate, timeout=timeout)
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
            if (cmd_char in buf) and ("vux>" in buf[buf.find(cmd_char) + len(cmd_char) :]):
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
            mode[1] |= termios.OPOST | termios.ONLCR
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


def build_vux9_image(
    file_or_bytes,
    slot=1,
    name="",
    mode="hack",
    version=1,
    load_addr=0x00000000,
    entry_point=0x00000000,
    crc_override=None,
    magic_override=None,
):
    """Pack binary payload into VUX9 v3 image with 64-byte boot header and 512-byte sector padding"""
    if isinstance(file_or_bytes, (bytes, bytearray)):
        payload = bytes(file_or_bytes)
        default_name = f"slot{slot}"
    else:
        if not os.path.exists(file_or_bytes):
            raise FileNotFoundError(f"File {file_or_bytes} not found!")
        with open(file_or_bytes, "rb") as f:
            payload = f.read()
        default_name = os.path.splitext(os.path.basename(file_or_bytes))[0]

    mode_val = MODE_HACK if mode == "hack" else MODE_RISCV
    if mode_val == MODE_HACK:
        # Pre-pack Hack 16-bit big-endian binary into native 32-bit LE word order for I-RAM
        packed_payload = bytearray()
        for i in range(0, len(payload), 4):
            chunk = payload[i : i + 4]
            if len(chunk) < 4:
                chunk = chunk + b"\x00" * (4 - len(chunk))
            instr0 = (chunk[0] << 8) | chunk[1]
            instr1 = (chunk[2] << 8) | chunk[3]
            word = (instr1 << 16) | instr0
            packed_payload.extend(struct.pack("<I", word))
        final_payload = bytes(packed_payload)
    else:
        final_payload = payload

    size_bytes = len(final_payload)
    if not name:
        name = default_name
    crc_val = crc_override if crc_override is not None else (binascii.crc32(final_payload) & 0xFFFFFFFF)
    header = SlotHeader(
        mode=mode_val,
        size=size_bytes,
        crc32=crc_val,
        name=name.encode("ascii", errors="replace"),
        version=version,
        load_addr=load_addr,
        entry_point=entry_point,
        flags=FLAG_VALID | (FLAG_SYSTEM if slot == 0 else 0),
    )
    header_ver = header.header_version
    raw_data = header.pack(magic_override if magic_override is not None else VUX_MAGIC) + final_payload

    rem = len(raw_data) % SECTOR
    if rem != 0:
        raw_data = raw_data + b"\x00" * (SECTOR - rem)

    num_sectors = len(raw_data) // SECTOR
    if num_sectors > SLOT_SECTORS:
        raise ValueError(f"Binary with header ({num_sectors} sectors) exceeds 32KB slot limit (64 sectors)!")

    start_sector = slot_sector(slot)
    meta = {
        "slot": slot,
        "name": name,
        "mode": mode,
        "mode_val": mode_val,
        "header_ver": header_ver,
        "size_bytes": size_bytes,
        "load_addr": load_addr,
        "entry_point": entry_point,
        "crc32": crc_val,
        "version": version,
        "start_sector": start_sector,
        "num_sectors": num_sectors,
        "total_bytes": len(raw_data),
    }
    return raw_data, meta


def flash_slot(
    ser,
    file_or_bytes,
    slot=1,
    name="",
    mode="hack",
    version=1,
    progress_cb=None,
    crc_override=None,
    magic_override=None,
):
    """Flash a binary image or byte payload to SD Card Slot 0-9 using an open serial port"""
    raw_data, meta = build_vux9_image(
        file_or_bytes,
        slot=slot,
        name=name,
        mode=mode,
        version=version,
        crc_override=crc_override,
        magic_override=magic_override,
    )
    num_sectors = meta["num_sectors"]
    start_sector = meta["start_sector"]

    # 1. Wait for [READY] with gentle retry
    buf = b""
    ready = False
    for _attempt in range(3):
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
        raise RuntimeError(
            f"Timeout waiting for token {slot_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}"
        )

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
        raise RuntimeError(
            f"Timeout waiting for token {count_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}"
        )

    # 4. Stream sectors with per-sector handshake
    for sec_idx in range(num_sectors):
        sec_token = f"[READY-SEC:{sec_idx}]".encode("utf-8")
        start = time.time()
        ready_sec = False
        while time.time() - start < 10.0:
            if sec_token in buf:
                buf = buf[buf.find(sec_token) + len(sec_token) :]
                ready_sec = True
                break
            c = ser.read(64)
            if c:
                buf += c
                if sec_token in buf:
                    buf = buf[buf.find(sec_token) + len(sec_token) :]
                    ready_sec = True
                    break

        if not ready_sec:
            raise RuntimeError(
                f"Timeout waiting for token {sec_token.decode()}. Output: {buf.decode('utf-8', errors='replace')}"
            )

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

    completion_output = buf.decode("utf-8", errors="replace")
    meta["completion_output"] = completion_output

    # main.rs always reprints "vux> " after write_sectors_from_uart() returns,
    # whether it succeeded or hit an [SD-ERR] failure partway through -- so
    # the prompt reappearing above is NOT proof of success on its own.
    if "[SD-ERR]" in completion_output:
        raise RuntimeError(f"SD card error during flash: {completion_output}")

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
        print("=== Preparing VUX9 v3 Slot Boot Image ===")
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
        print("\n=== Multi-Sector Flash & Verification Complete! ===")
    except Exception as e:
        print(f"Error flashing slot: {e}")
        sys.exit(1)
    finally:
        ser.close()


def cmd_reset(args):
    """Trigger FPGA hardware reset via openFPGALoader --reset"""
    import shutil
    import subprocess

    loader_bin = shutil.which("openFPGALoader")
    if not loader_bin:
        home_cad = os.path.expanduser("~/.local/oss-cad-suite/bin/openFPGALoader")
        if os.path.exists(home_cad):
            loader_bin = home_cad
        else:
            print("Error: openFPGALoader not found in PATH or ~/.local/oss-cad-suite/bin")
            sys.exit(1)

    print(f"Triggering Tang Nano 9K hardware reset via {loader_bin} -b tangnano9k --reset...")
    try:
        subprocess.run([loader_bin, "-b", "tangnano9k", "--reset"], check=True)
        print("Hardware reset completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to reset FPGA: {e}")
        sys.exit(1)


def build_sd_image(slots, mbr=True):
    """Raw SD card image (sector n at byte 512 n), as `flash-sd` would leave the card.

    slots: iterable of (slot, file_or_bytes, mode, name). Sector 0 carries the 0x55AA
    MBR signature unless mbr=False. Returns bytes, sized up to the last slot's sectors.
    """
    img = bytearray(512)
    if mbr:
        img[510:512] = b"\x55\xaa"
    for slot, src, mode, name in slots:
        raw, meta = build_vux9_image(src, slot=slot, name=name, mode=mode)
        start = slot_sector(slot) * SECTOR
        if len(img) < start + len(raw):
            img.extend(bytes(start + len(raw) - len(img)))
        img[start : start + len(raw)] = raw
    return bytes(img)


def cmd_mkimg(args):
    slots = []
    for spec in args.slot:
        parts = spec.split(":", 3)
        if len(parts) < 3 or not parts[0].isdigit() or parts[2] not in ("hack", "riscv"):
            print(f"Invalid --slot {spec!r}: expected N:FILE:hack|riscv[:NAME]")
            sys.exit(2)
        slot = int(parts[0])
        if not 0 <= slot <= 9:
            print(f"Invalid slot {slot}: 0-9")
            sys.exit(2)
        slots.append((slot, parts[1], parts[2], parts[3] if len(parts) > 3 else ""))
    img = build_sd_image(slots, mbr=not args.no_mbr)
    with open(args.output, "wb") as f:
        f.write(img)
    print(f"Wrote {args.output}: {len(img) // 512} sectors, slots {sorted(s[0] for s in slots) or 'none'}")


def main():
    parser = argparse.ArgumentParser(description="VUX9K Host Tooling")
    parser.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # reset
    subparsers.add_parser("reset", help="Trigger FPGA hardware reset via openFPGALoader")

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

    # mkimg
    sub_img = subparsers.add_parser("mkimg", help="Build a raw SD card image (for the emulator's --sd)")
    sub_img.add_argument("output", help="Image file to write")
    sub_img.add_argument(
        "--slot",
        action="append",
        default=[],
        metavar="N:FILE:MODE[:NAME]",
        help="Put FILE in slot N (0-9) for ISA MODE (hack|riscv); repeatable",
    )
    sub_img.add_argument("--no-mbr", action="store_true", help="Leave sector 0 without the 0x55AA signature")

    args = parser.parse_args()

    if args.command == "mkimg":
        cmd_mkimg(args)
        return
    if args.command == "reset":
        cmd_reset(args)
    elif args.command == "monitor":
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
