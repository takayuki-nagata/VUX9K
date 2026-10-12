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
import glob
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

# VUX9 v4: the header of a file the two-stage boot loads (fw_common's ImageHeader). The
# same 64-byte layout as v3; v3's flags are reserved (0). The file is the header, then
# the payload, with no padding.
HEADER_VERSION_V4 = 4
XMEM_BASE = 0x80000000  # the PSRAM, where RV32 images load by default


@dataclass(frozen=True)
class ImageHeader:
    mode: int
    size: int
    crc32: int
    name: bytes = b""
    version: int = 1
    load_addr: int = 0
    entry_point: int = 0

    def pack(self, magic=VUX_MAGIC, header_version=HEADER_VERSION_V4, reserved=0):
        return struct.pack(
            HEADER_FORMAT,
            magic,
            header_version,
            reserved,
            self.mode,
            self.size,
            self.load_addr,
            self.entry_point,
            self.crc32,
            self.version,
            self.name[:32].ljust(32, b"\x00"),
        )

    @classmethod
    def parse(cls, data):
        """The v4 header at the start of data if the firmware accepts it (magic, version 4,
        reserved 0; fw_common's ImageHeader::parse), else None."""
        if len(data) < HEADER_LEN:
            return None
        magic, hver, reserved, mode, size, load, entry, crc, ver, name = struct.unpack_from(HEADER_FORMAT, data)
        if magic != VUX_MAGIC or hver != HEADER_VERSION_V4 or reserved != 0:
            return None
        return cls(mode, size, crc, name, ver, load, entry)


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


# The board's USB-UART bridge (a BL702 that emulates an FT2232) keeps one line setting for
# both of its interfaces: SET_BAUD, on either interface, only stores a rate, and SET_DATA, on
# either, restarts the UART at the stored rate. Linux's ftdi_sio sends SET_DATA before
# SET_BAUD when a port opens, so a freshly opened port runs at the rate stored last (57600
# after ModemManager probed the JTAG tty, see modemmanager_hint) until the next SET_DATA.
def apply_line_settings(ser):
    """Make the bridge apply this port's own rate: set the line format twice (two SET_DATA)."""
    if serial is None or not isinstance(ser, serial.Serial):
        return  # the emulator's socket, or pyftdi (no kernel driver in between)
    stopbits = ser.stopbits
    ser.stopbits = serial.STOPBITS_TWO if stopbits == serial.STOPBITS_ONE else serial.STOPBITS_ONE
    ser.stopbits = stopbits


def _read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def modemmanager_hint(tty, sysfs="/sys", run="/run", proc="/proc"):
    """Why ModemManager can garble the board's UART, or "" if it can't.

    ModemManager probes every new serial port. openFPGALoader hands the bridge's JTAG
    interface back as a new tty after each load; ModemManager opens it at 57600 baud and the
    bridge applies that rate to the board's UART too, now or at the next open (see
    apply_line_settings). Harmless when ModemManager isn't running or every tty of the
    board carries ID_MM_DEVICE_IGNORE=1 or ID_MM_PORT_IGNORE=1 (tools/70-vux9k-board.rules).
    Only usb-serial ttys (ttyUSBn, as the board's ftdi_sio ports) are looked at.
    """
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return ""
    if not any(_read_text(os.path.join(proc, p, "comm")).strip() == "ModemManager" for p in pids):
        return ""
    name = os.path.basename(os.path.realpath(tty))
    dev = os.path.realpath(os.path.join(sysfs, "class", "tty", name, "device"))
    while dev != os.path.dirname(dev) and not os.path.isfile(os.path.join(dev, "idVendor")):
        dev = os.path.dirname(dev)
    if not os.path.isfile(os.path.join(dev, "idVendor")):
        return ""  # not a USB tty (e.g. the emulator)
    # usb-serial: <device>/<interface>/ttyUSBn/tty/ttyUSBn (ftdi_sio, as on the board)
    ttys = sorted({os.path.basename(t) for t in glob.glob(os.path.join(dev, "*", "*", "tty", "*"))})
    probed, unknown = [], []
    for t in ttys:
        devno = _read_text(os.path.join(sysfs, "class", "tty", t, "dev")).strip()
        props = _read_text(os.path.join(run, "udev", "data", "c" + devno)).splitlines() if devno else []
        if not props:
            unknown.append(t)  # udev's database not visible here (e.g. a container)
        elif not {"E:ID_MM_DEVICE_IGNORE=1", "E:ID_MM_PORT_IGNORE=1"} & set(props):
            probed.append(t)
    if not probed and not unknown:
        return ""
    why = f"may probe the board's {', '.join(probed)}" if probed else ""
    if unknown:
        why += ("; " if why else "") + f"can't tell whether it ignores {', '.join(unknown)} (no udev data readable)"
    return (
        f"ModemManager is running and {why}: its probe sets 57600 baud, which the board's USB bridge "
        "applies to the board's UART and garbles its output. Install tools/70-vux9k-board.rules "
        "(instructions in the file) or stop ModemManager."
    )


def garbled(data):
    """True if data (bytes, or text decoded with errors="replace") holds what the Boot Manager
    and the demos never send: non-ASCII or control bytes, i.e. the UART ran at another rate."""
    codes = data if isinstance(data, (bytes, bytearray)) else [ord(c) for c in data]
    return any(c >= 0x80 or (c < 0x20 and c not in (0x09, 0x0A, 0x0D, 0x1B)) for c in codes)


def garbled_hint(data):
    """A note for a failure message when the output is garbled, else ""."""
    if not garbled(data):
        return ""
    return (
        "the output is garbled: the board's USB bridge received it at another baud rate "
        "(ModemManager probing the board? see tools/70-vux9k-board.rules)"
    )


def open_port(port_name="auto", baudrate=115200, timeout=0.2):
    if port_name == "auto":
        port = find_tangnano_uart_port()
        if port is not None and serial is not None:
            try:
                ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
            except Exception:
                pass
            else:
                apply_line_settings(ser)
                return ser

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
        apply_line_settings(ser)
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


def send_cmd_and_wait(ser, cmd_char, timeout=5.0, prompt="vux>"):
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
            if (cmd_char in buf) and (prompt in buf[buf.find(cmd_char) + len(cmd_char) :]):
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


def _read_payload(file_or_bytes):
    """(payload, default name): the bytes as given, or a file's contents and stem."""
    if isinstance(file_or_bytes, (bytes, bytearray)):
        return bytes(file_or_bytes), None
    if not os.path.exists(file_or_bytes):
        raise FileNotFoundError(f"File {file_or_bytes} not found!")
    with open(file_or_bytes, "rb") as f:
        return f.read(), os.path.splitext(os.path.basename(file_or_bytes))[0]


def pack_hack(payload):
    """A Hack binary (16-bit big-endian instructions) in the 32-bit little-endian words
    the Hack CPU fetches: two instructions per word, the first in the low half."""
    packed = bytearray()
    for i in range(0, len(payload), 4):
        chunk = payload[i : i + 4].ljust(4, b"\x00")
        instr0 = (chunk[0] << 8) | chunk[1]
        instr1 = (chunk[2] << 8) | chunk[3]
        packed.extend(struct.pack("<I", (instr1 << 16) | instr0))
    return bytes(packed)


def build_vux9_v4_image(
    file_or_bytes,
    name="",
    mode="riscv",
    version=1,
    load_addr=None,
    entry_point=None,
    crc_override=None,
    magic_override=None,
):
    """A VUX9 v4 file: the 64-byte header, then the payload (Hack binaries packed as
    pack_hack does). load_addr defaults to XMEM_BASE for RV32 and 0 for Hack,
    entry_point to load_addr. Returns (bytes, ImageHeader)."""
    payload, default_name = _read_payload(file_or_bytes)
    mode_val = MODE_HACK if mode == "hack" else MODE_RISCV
    if mode_val == MODE_HACK:
        payload = pack_hack(payload)
    if load_addr is None:
        load_addr = XMEM_BASE if mode_val == MODE_RISCV else 0
    header = ImageHeader(
        mode=mode_val,
        size=len(payload),
        crc32=crc_override if crc_override is not None else binascii.crc32(payload) & 0xFFFFFFFF,
        name=(name or default_name or "").encode("ascii", errors="replace"),
        version=version,
        load_addr=load_addr,
        entry_point=load_addr if entry_point is None else entry_point,
    )
    magic = magic_override if magic_override is not None else VUX_MAGIC
    return header.pack(magic) + payload, header


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
    payload, default_name = _read_payload(file_or_bytes)
    default_name = default_name or f"slot{slot}"

    mode_val = MODE_HACK if mode == "hack" else MODE_RISCV
    final_payload = pack_hack(payload) if mode_val == MODE_HACK else payload

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


UPLOAD_V2_BLOCK = 512


class UploadError(Exception):
    """A transfer v2 that failed: the device's [ERR] line, or what the host saw."""


def _upload_line(ser, deadline):
    """The device's next transfer line ([READY...], [LEN:...], [OK]..., [ERR]...); other
    lines (echo, banners) are skipped."""
    buf = b""
    while time.monotonic() < deadline:
        buf += ser.read_until(b"\n")
        if buf.endswith(b"\n"):
            line = buf.decode("ascii", errors="replace").strip()
            buf = b""
            if line.startswith(("[READY", "[LEN:", "[OK]", "[ERR]")):
                return line
    raise UploadError(f"no answer from the device (received {buf!r})")


def _upload_expect(ser, want, deadline):
    line = _upload_line(ser, deadline)
    if line.startswith("[ERR]") or not line.startswith(want):
        raise UploadError(line)
    return line


def send_upload_v2(ser, data, progress_cb=None, timeout=10.0, crc_override=None):
    """Send data with transfer v2 (fw_common::upload::upload_v2), once the device's command
    for it has been given: the length, 512-byte blocks each after the device's
    [READY-BLK:i], then the CRC-32. Returns the device's [OK] line; raises UploadError
    with its [ERR] line. timeout applies to each line."""

    def deadline():
        return time.monotonic() + timeout

    _upload_expect(ser, "[READY-V2]", deadline())
    ser.write(struct.pack("<I", len(data)))
    ser.flush()
    _upload_expect(ser, f"[LEN:{len(data)}]", deadline())
    blocks = range(0, len(data), UPLOAD_V2_BLOCK)
    for i, at in enumerate(blocks):
        _upload_expect(ser, f"[READY-BLK:{i}]", deadline())
        ser.write(data[at : at + UPLOAD_V2_BLOCK])
        ser.flush()
        if progress_cb:
            progress_cb(i, len(blocks))
    _upload_expect(ser, "[READY-CRC]", deadline())
    crc = crc_override if crc_override is not None else binascii.crc32(data) & 0xFFFFFFFF
    ser.write(struct.pack("<I", crc))
    ser.flush()
    return _upload_expect(ser, "[OK]", deadline())


# ----- The two-stage boot: the first stage's prompt, RAM boot, second-stage install -----

S1_PROMPT = "s1> "
S2_PROMPT = "s2> "


def read_until(ser, tokens, timeout):
    """Read until one of tokens arrives: (token or None on a timeout, text read)."""
    text = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        c = ser.read(256)
        if c:
            text += c.decode("utf-8", errors="replace")
            for t in tokens:
                if t in text:
                    return t, text
    return None, text


def enter_s1(ser, timeout=10.0):
    """Get to the first stage's prompt: soft-reset a second stage found at its prompt,
    and send keys until the first stage answers (a key right after its banner keeps it
    there instead of booting). Returns the text read; raises TimeoutError."""
    drain_serial(ser, timeout=0.05)
    text = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ser.write(b"\r")
        ser.flush()
        got, t = read_until(ser, (S1_PROMPT, S2_PROMPT), 0.02)
        text += t
        if got == S1_PROMPT:
            return text
        if got == S2_PROMPT:
            ser.write(b"r")
            ser.flush()
    raise TimeoutError(f"no first-stage prompt; got {text[-400:]!r}")


def ram_boot(ser, data, timeout=10.0):
    """At the first stage's prompt: send the v4 file data with `u` and wait for the
    second stage's prompt. Returns the text from the [OK] line on; raises UploadError with
    the first stage's reason when it refuses."""
    drain_serial(ser, timeout=0.05)
    ser.write(b"u")
    ser.flush()
    try:
        ok = send_upload_v2(ser, data, timeout=timeout)
    except UploadError as e:
        _, rest = read_until(ser, (S1_PROMPT,), timeout)
        raise UploadError(f"{e} {rest.strip()}") from None
    got, text = read_until(ser, (S2_PROMPT, S1_PROMPT), timeout)
    if got != S2_PROMPT:
        raise UploadError(f"{ok}: no second stage started; got {text!r}")
    return ok + "\n" + text


def install_stage2(ser, data, unchecked=False, timeout=30.0):
    """At the second stage's prompt: install the v4 file data as \\VUX9K\\STAGE2.BIN
    (`n`; the previous one becomes STAGE2.OLD), or write it there unchecked (`x`, tests).
    Returns the result line; raises UploadError unless it ends in OK."""
    drain_serial(ser, timeout=0.05)
    ser.write(b"x" if unchecked else b"n")
    ser.flush()
    send_upload_v2(ser, data, timeout=timeout)
    got, text = read_until(ser, (S2_PROMPT,), timeout)
    line = next((ln for ln in text.splitlines() if ln.startswith(("[INSTALL]", "[UNCHECKED]"))), "")
    if not line.endswith("OK"):
        raise UploadError(line or text)
    return line


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


FAT_PART_LBA = 2048  # where SD cards start their first partition
FAT_RESERVED = 32
FAT_DATE_1980 = (1 << 5) | 1  # 1980-01-01 as a FAT date; images carry no time
FAT_EOC = 0x0FFFFFFF


def fat_short_name(name):
    """name as an 11-byte 8.3 directory name (upper case, space-padded); ValueError if it
    isn't one (fw_common::fat::to_short_name accepts the same)."""
    if not name.isascii():
        # str.upper() would turn some non-ASCII letters into ASCII ones ("ß" -> "SS")
        raise ValueError(f"{name!r} is not an 8.3 name")
    base, dot, ext = name.upper().rpartition(".")
    if not dot:
        base, ext = ext, ""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~")
    if not (1 <= len(base) <= 8 and len(ext) <= 3 and (ext or not dot)) or not set(base + ext) <= allowed:
        raise ValueError(f"{name!r} is not an 8.3 name")
    return base.ljust(8).encode() + ext.ljust(3).encode()


def _fat_entry(name11, attr, cluster, size):
    return struct.pack(
        "<11sBBBHHHHHHHI",
        name11,
        attr,
        0,  # NTRes
        0,  # creation time, tenths
        0,  # creation time
        FAT_DATE_1980,  # creation date
        FAT_DATE_1980,  # access date
        cluster >> 16,
        0,  # write time
        FAT_DATE_1980,  # write date
        cluster & 0xFFFF,
        size,
    )


def build_fat32_image(files, part_sectors=8192, cluster_sectors=1, label="VUX9K"):
    """A card image: an MBR with one FAT32 partition (type 0x0C) at LBA 2048 holding
    `files` ({"DIR/NAME.EXT": bytes}; 8.3 names, directories made as needed). FAT32 by its
    BPB (FATSz16 = 0, TotSec32) even when it has fewer clusters than FAT32 normally
    needs, as fw_common::fat accepts: small images for the emulator and the tests. Two
    FATs, FSInfo with the real free count, backup boot sector; reproducible byte for
    byte (fixed volume ID, 1980-01-01 dates)."""
    if cluster_sectors not in (1, 2, 4, 8, 16, 32, 64, 128):
        raise ValueError("cluster_sectors must be a power of two up to 128")
    # FAT size: enough entries for the clusters left after the FATs themselves
    fat_sectors = 1
    while True:
        clusters = (part_sectors - FAT_RESERVED - 2 * fat_sectors) // cluster_sectors
        if (clusters + 2) * 4 <= fat_sectors * SECTOR:
            break
        fat_sectors += 1
    cluster_bytes = cluster_sectors * SECTOR
    fat = [0x0FFFFFF8, FAT_EOC]
    data = {}  # cluster -> bytes

    def alloc(content):
        """Clusters for content (at least one); returns the first."""
        n = max(1, -(-len(content) // cluster_bytes))
        first = len(fat)
        if first + n > clusters + 2:
            raise ValueError("files don't fit the image")
        for i in range(n):
            fat.append(first + i + 1 if i < n - 1 else FAT_EOC)
            data[first + i] = content[i * cluster_bytes : (i + 1) * cluster_bytes]
        return first

    # The tree: {name11: bytes | dict}
    tree: dict[bytes, object] = {}
    for path, content in files.items():
        parts = [fat_short_name(p) for p in path.replace("\\", "/").split("/") if p]
        node = tree
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"{path}: a file is in the way")
            node = child
        if parts[-1] in node:
            raise ValueError(f"{path} given twice")
        node[parts[-1]] = bytes(content)

    def write_dir(node, own, parent, root=False):
        """Directory `node` into cluster(s) from `own` (pre-allocated first cluster)."""
        entries = []
        if root:
            entries.append(_fat_entry(label.upper().ljust(11)[:11].encode(), 0x08, 0, 0))
        else:
            entries.append(_fat_entry(b".          ", 0x10, own, 0))
            entries.append(_fat_entry(b"..         ", 0x10, parent, 0))
        for name, child in node.items():
            if isinstance(child, dict):
                first = alloc(b"")
                entries.append(_fat_entry(name, 0x10, first, 0))
                write_dir(child, first, 0 if root else own)
            else:
                first = alloc(child) if child else 0
                entries.append(_fat_entry(name, 0x20, first, len(child)))
        raw = b"".join(entries)
        # The directory's own clusters: the first is `own`, more are chained on
        n = max(1, -(-len(raw) // cluster_bytes))
        chain = [own] + [alloc(b"") for _ in range(n - 1)]
        for a, b in zip(chain, chain[1:], strict=False):
            fat[a] = b
        for i, c in enumerate(chain):
            data[c] = raw[i * cluster_bytes : (i + 1) * cluster_bytes]

    root_cluster = alloc(b"")
    write_dir(tree, root_cluster, 0, root=True)
    used = len(fat) - 2

    boot = bytearray(SECTOR)
    boot[0:3] = b"\xeb\x58\x90"
    boot[3:11] = b"VUX9K   "
    struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, cluster_sectors, FAT_RESERVED, 2, 0, 0, 0xF8, 0, 63, 255,
                     FAT_PART_LBA, part_sectors)  # fmt: skip
    struct.pack_into("<IHHIHH", boot, 36, fat_sectors, 0, 0, root_cluster, 1, 6)
    struct.pack_into(
        "<BBBI11s8s", boot, 64, 0x80, 0, 0x29, VUX_MAGIC, label.upper().ljust(11)[:11].encode(), b"FAT32   "
    )
    boot[510:512] = b"\x55\xaa"
    fsinfo = bytearray(SECTOR)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<IIII", fsinfo, 484, 0x61417272, clusters - used, len(fat), 0)
    struct.pack_into("<I", fsinfo, 508, 0xAA550000)

    part = bytearray(part_sectors * SECTOR)
    for lba, sector in ((0, boot), (1, fsinfo), (6, boot), (7, fsinfo)):
        part[lba * SECTOR : (lba + 1) * SECTOR] = sector
    fat_raw = struct.pack(f"<{len(fat)}I", *fat)
    for copy in range(2):
        at = (FAT_RESERVED + copy * fat_sectors) * SECTOR
        part[at : at + len(fat_raw)] = fat_raw
    data_at = (FAT_RESERVED + 2 * fat_sectors) * SECTOR
    for c, content in data.items():
        at = data_at + (c - 2) * cluster_bytes
        part[at : at + len(content)] = content

    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\xfe\xff\xff", 0x0C, b"\xfe\xff\xff", FAT_PART_LBA, part_sectors)
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr) + bytes((FAT_PART_LBA - 1) * SECTOR) + bytes(part)


def cmd_mkfat(args):
    files = {}
    for spec in args.file:
        dest, sep, src = spec.partition("=")
        if not sep:
            print(f"Invalid --file {spec!r}: expected PATH=FILE (e.g. VUX9K/STAGE2.BIN=stage2.bin)")
            sys.exit(2)
        with open(src, "rb") as f:
            files[dest] = f.read()
    try:
        img = build_fat32_image(files, part_sectors=args.size_mb * 2048, cluster_sectors=args.cluster_sectors)
    except ValueError as e:
        print(f"Cannot build the image: {e}")
        sys.exit(2)
    with open(args.output, "wb") as f:
        f.write(img)
    print(f"Wrote {args.output}: FAT32, {args.size_mb} MB partition, {len(files)} files")


def cmd_mkv4(args):
    load_addr = int(args.load_addr, 0) if args.load_addr else None
    entry = int(args.entry, 0) if args.entry else None
    data, _ = build_vux9_v4_image(
        args.input, name=args.name, mode=args.mode, version=args.version, load_addr=load_addr, entry_point=entry
    )
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"Wrote {args.output}: VUX9 v4, {len(data) - HEADER_LEN} bytes payload")


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


def cmd_sd_info(args):
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s1(ser)
        print(send_cmd_and_wait(ser, "i", timeout=30.0, prompt=S1_PROMPT))
    finally:
        ser.close()


def cmd_run(args):
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s1(ser)
        print(ram_boot(ser, _read_file(args.file)))
    finally:
        ser.close()


def cmd_install_stage2(args):
    data = _read_file(args.file)
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s1(ser)
        ram_boot(ser, data)
        print(install_stage2(ser, data))
        ser.write(b"r")
        ser.flush()
        _, text = read_until(ser, (S2_PROMPT, S1_PROMPT), 20.0)
        print(text)
    finally:
        ser.close()


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

    # mkfat
    sub_fat = subparsers.add_parser("mkfat", help="Build a FAT32 SD card image file (one partition, 8.3 names)")
    sub_fat.add_argument("output", help="Image file to write")
    sub_fat.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH=FILE",
        help="Put FILE at PATH (8.3 names, e.g. VUX9K/STAGE2.BIN); repeatable",
    )
    sub_fat.add_argument("--size-mb", type=int, default=4, help="Partition size in MB (default: 4)")
    sub_fat.add_argument("--cluster-sectors", type=int, default=1, help="Sectors per cluster (default: 1)")

    # mkv4
    sub_v4 = subparsers.add_parser("mkv4", help="Wrap a binary in a VUX9 v4 header (a file for the card)")
    sub_v4.add_argument("input", help="Binary to wrap")
    sub_v4.add_argument("output", help="v4 file to write")
    sub_v4.add_argument("--mode", choices=["hack", "riscv"], default="riscv", help="Target ISA mode")
    sub_v4.add_argument("--name", default="", help="Program name (max 32 characters)")
    sub_v4.add_argument("--version", type=int, default=1, help="Version number (default: 1)")
    sub_v4.add_argument("--load-addr", help="Load address (default: 0x80000000 for RV32, 0 for Hack)")
    sub_v4.add_argument("--entry", help="Entry point (default: the load address)")

    # two-stage boot
    subparsers.add_parser("sd-info", help="SD card, FAT32 and STAGE2 files (first stage's i)")
    sub_run = subparsers.add_parser("run", help="RAM-boot a second stage (v4 file) through the first stage")
    sub_run.add_argument("file", help="v4 file")
    sub_inst = subparsers.add_parser(
        "install-stage2", help="Install a second stage (v4 file) as \\VUX9K\\STAGE2.BIN and boot it"
    )
    sub_inst.add_argument("file", help="v4 file (it is RAM-booted, then installs itself)")

    args = parser.parse_args()

    if args.command == "mkv4":
        cmd_mkv4(args)
        return
    if args.command == "mkimg":
        cmd_mkimg(args)
        return
    if args.command == "mkfat":
        cmd_mkfat(args)
        return
    tty = find_tangnano_uart_port() if args.port == "auto" else args.port
    hint = modemmanager_hint(tty) if tty and "://" not in tty else ""
    if hint:
        print(f"Warning: {hint}", file=sys.stderr)
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
    elif args.command == "sd-info":
        cmd_sd_info(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "install-stage2":
        cmd_install_stage2(args)


if __name__ == "__main__":
    main()
