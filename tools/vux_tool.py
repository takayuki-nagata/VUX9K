#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Host Tooling (vux_tool.py)
For the two-stage boot on the Tang Nano 9K and its emulator:
- a serial terminal (monitor) and the FPGA reset,
- VUX9 v4 files (mkv4) and FAT32 card images (mkfat),
- the first stage's prompt: card report (sd-info), RAM boot of a second stage (run),
  and installing a second stage as \\VUX9K\\STAGE2.BIN (install-stage2),
- the second stage's prompt: files on the card (ls, put) and starting an application
  (run-app),
- transfer v2 (send_upload_v2) and the functions the tests use.
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

# The VUX9 v4 file header, as firmware/fw_common/src/header.rs (the firmware's side)
# defines it; sim/emu/test_vux9_header.py keeps the two in step.
VUX_MAGIC = 0x56555839  # "VUX9"
HEADER_LEN = 64
SECTOR = 512
MODE_HACK = 0
MODE_RISCV = 1
# magic, header_version (u16), reserved (u16, 0), mode, size, load_addr, entry_point,
# crc32, version (the application's), name (32 bytes, NUL-padded); little-endian
HEADER_FORMAT = "<IHHIIIIII32s"
# The first stage's and the second stage's prompts
S1_PROMPT = "s1> "
S2_PROMPT = "s2> "


HEADER_VERSION_V4 = 4
XMEM_BASE = 0x80000000  # the PSRAM, where RV32 images load by default
# Hack's segments in the PSRAM (soc_pkg::HACK_I_BASE/HACK_D_BASE): a Hack image's load
# address is a byte offset in the 64 KB instruction segment (default 0; it starts at 0),
# its data word A is the 32-bit word at HACK_D_BASE + 4 * A
HACK_I_BASE = 0x80000000
HACK_I_BYTES = 0x10000
HACK_D_BASE = 0x80020000


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


def modemmanager_hint(tty, sysfs="/sys", run="/run", proc="/proc", settle=2.0):
    """Why ModemManager can garble the board's UART, or "" if it can't.

    ModemManager probes every new serial port. openFPGALoader hands the bridge's JTAG
    interface back as a new tty after each load; ModemManager opens it at 57600 baud and the
    bridge applies that rate to the board's UART too, now or at the next open (see
    apply_line_settings). Harmless when ModemManager isn't running or every tty of the
    board carries ID_MM_DEVICE_IGNORE=1 or ID_MM_PORT_IGNORE=1 (tools/70-vux9k-board.rules).
    Only usb-serial ttys (ttyUSBn, as the board's ftdi_sio ports) are looked at. A tty
    openFPGALoader has just handed back has no udev data for ~20 ms (measured 2026-10-15),
    until udev has run its rules: while udev's database exists, a tty without data is
    waited for up to settle seconds.
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
    deadline = time.monotonic() + settle if os.path.isdir(os.path.join(run, "udev", "data")) else 0.0
    while True:
        probed, unknown = [], []
        for t in ttys:
            devno = _read_text(os.path.join(sysfs, "class", "tty", t, "dev")).strip()
            props = _read_text(os.path.join(run, "udev", "data", "c" + devno)).splitlines() if devno else []
            if not props:
                unknown.append(t)  # udev not done with it yet, or its database not visible here
            elif not {"E:ID_MM_DEVICE_IGNORE=1", "E:ID_MM_PORT_IGNORE=1"} & set(props):
                probed.append(t)
        if not unknown or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
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
    """True if data (bytes, or text decoded with errors="replace") holds what the firmware
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


def send_cmd_and_wait(ser, cmd_char, timeout=5.0, prompt=S1_PROMPT):
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
    pack_hack does). load_addr defaults to XMEM_BASE for RV32 and 0 for Hack (an offset in
    Hack's instruction segment, HACK_I_BASE), entry_point to load_addr. Returns (bytes,
    ImageHeader)."""
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


def read_until(ser, tokens, timeout, text=""):
    """Read until one of tokens arrives: (token or None on a timeout, text read). text is
    output already read (e.g. `after_run`'s), searched first and returned in front."""
    for t in tokens:
        if t in text:
            return t, text
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        c = ser.read(256)
        if c:
            text += c.decode("utf-8", errors="replace")
            for t in tokens:
                if t in text:
                    return t, text
    return None, text


def enter_s1(ser, timeout=10.0, drain=True):
    """Get to the first stage's prompt: soft-reset a second stage found at its prompt,
    and send keys until the first stage answers (a key right after its banner keeps it
    there instead of booting). Returns the text read (without what arrived before, unless
    drain=False); raises TimeoutError."""
    if drain:
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
            ser.write(b"reset\r")
            ser.flush()
    raise TimeoutError(f"no first-stage prompt; got {text[-400:]!r}")


def enter_s2(ser, timeout=20.0):
    """Get to the second stage's prompt: Enter until a prompt answers, and from the first
    stage's, boot the card's second stage (`b`). Returns the text read; raises
    TimeoutError."""
    drain_serial(ser, timeout=0.05)
    text = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ser.write(b"\r")
        ser.flush()
        got, t = read_until(ser, (S1_PROMPT, S2_PROMPT), 0.05)
        text += t
        if got == S2_PROMPT:
            return text
        if got == S1_PROMPT:
            ser.write(b"b")
            ser.flush()
            got, t = read_until(ser, (S2_PROMPT,), max(0.0, end - time.monotonic()))
            text += t
            if got:
                return text
    raise TimeoutError(f"no second-stage prompt; got {text[-400:]!r}")


class S2Error(Exception):
    """A second-stage command that failed: its [ERR] line."""


def _s2_reply(text, line):
    """What a command printed: text without the echo of its line and the prompt."""
    echo = line + "\n"
    at = text.find(echo)
    text = text[at + len(echo) :] if at >= 0 else text
    return text[: text.rfind(S2_PROMPT)] if S2_PROMPT in text else text


def _s2_check(out):
    err = next((ln for ln in out.splitlines() if ln.startswith("[ERR]")), None)
    if err:
        raise S2Error(err)
    return out


def s2_cmd(ser, line, timeout=10.0, check=True):
    """At the second stage's prompt: run the command line and return what it printed
    (without its echo and the next prompt). With check, a reply with an [ERR] line raises
    S2Error; no prompt within timeout raises TimeoutError."""
    drain_serial(ser, timeout=0.05)
    ser.write(line.encode() + b"\r")
    ser.flush()
    got, text = read_until(ser, (line + "\n",), timeout)
    if got:
        # The reply and the prompt can come in the same read as the echo
        at = text.find(got) + len(got)
        got, rest = read_until(ser, (S2_PROMPT,), timeout, text[at:])
        text = text[:at] + rest
    if not got:
        raise TimeoutError(f"no prompt after {line!r}; got {text[-400:]!r}")
    out = _s2_reply(text, line)
    return _s2_check(out) if check else out


def put(ser, path, data, timeout=10.0):
    """At the second stage's prompt: write data to the card as path (8.3 names; an existing
    file is replaced) with `put` over transfer v2. Returns the [PUT] line; raises
    UploadError for the transfer, S2Error for the card."""
    drain_serial(ser, timeout=0.05)
    line = f"put {path}"
    ser.write(line.encode() + b"\r")
    ser.flush()
    try:
        send_upload_v2(ser, data, timeout=timeout)
    except UploadError:
        read_until(ser, (S2_PROMPT,), timeout)
        raise
    got, text = read_until(ser, (S2_PROMPT,), timeout + 0.002 * len(data))
    if not got:
        raise TimeoutError(f"no prompt after put; got {text[-400:]!r}")
    _s2_check(text)
    return next((ln for ln in text.splitlines() if ln.startswith("[PUT]")), text.strip())


def run_app(ser, path, timeout=30.0):
    """At the second stage's prompt: start the v4 file path on the card with `run`.
    Returns the text read: up to and including the [RUN] line, and whatever of the
    application's output arrived in the same read (an application in the PSRAM prints at
    once; `after_run` gives that part, the rest follows on ser). Raises S2Error when the
    second stage refuses it."""
    drain_serial(ser, timeout=0.05)
    ser.write(f"run {path}\r".encode())
    ser.flush()
    text = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _, t = read_until(ser, ("\n",), max(0.0, end - time.monotonic()))
        text += t
        at = text.find("[RUN] ")
        if at >= 0 and "\n" in text[at:]:
            return text
        if S2_PROMPT in text[text.find("\n") + 1 :]:
            _s2_check(text)
            raise S2Error(text.strip())
    raise TimeoutError(f"no [RUN] line after run {path}; got {text[-400:]!r}")


def after_run(text):
    """The application's output in `run_app`'s text: what follows the [RUN] line."""
    at = text.find("[RUN] ")
    return text[text.index("\n", at) + 1 :] if at >= 0 and "\n" in text[at:] else ""


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
    (`install`; the previous one becomes STAGE2.OLD, \\VUX9K\\ is made if missing), or write
    it there unchecked (`install -u`, tests). Returns the result line; raises UploadError
    unless it ends in OK."""
    drain_serial(ser, timeout=0.05)
    ser.write(b"install -u\r" if unchecked else b"install\r")
    ser.flush()
    send_upload_v2(ser, data, timeout=timeout)
    got, text = read_until(ser, (S2_PROMPT,), timeout)
    line = next((ln for ln in text.splitlines() if ln.startswith(("[INSTALL]", "[UNCHECKED]"))), "")
    if not line.endswith("OK"):
        raise UploadError(line or text)
    return line


# =====================================================================
# CLI Command Wrappers
# =====================================================================


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
        ser.write(b"reset\r")
        ser.flush()
        _, text = read_until(ser, (S2_PROMPT, S1_PROMPT), 20.0)
        print(text)
    finally:
        ser.close()


def cmd_ls(args):
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s2(ser)
        print(s2_cmd(ser, f"ls {args.path}" if args.path else "ls", timeout=30.0), end="")
    finally:
        ser.close()


def cmd_put(args):
    data = _read_file(args.file)
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s2(ser)
        print(put(ser, args.path, data))
    finally:
        ser.close()


def cmd_run_app(args):
    ser = open_port(args.port, baudrate=args.baud)
    try:
        enter_s2(ser)
        print(run_app(ser, args.path, timeout=args.timeout), end="")
    finally:
        ser.close()


def main():
    parser = argparse.ArgumentParser(description="VUX9K Host Tooling")
    parser.add_argument("--port", default="auto", help="Serial/FTDI port URL (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # reset
    subparsers.add_parser("reset", help="Trigger FPGA hardware reset via openFPGALoader")

    # monitor
    subparsers.add_parser("monitor", help="Open serial terminal monitor")

    # mkfat
    sub_fat = subparsers.add_parser("mkfat", help="Build a FAT32 SD card image file (one partition, 8.3 names)")
    sub_fat.add_argument("output", help="Image file to write")
    sub_fat.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH=FILE",
        help="Put FILE at PATH (8.3 names, directories made as needed, e.g. VUX9K/APPS/HELLO.BIN); repeatable",
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
    sub_v4.add_argument(
        "--load-addr",
        help="Load address (default: 0x80000000 for RV32; for Hack an offset in its 64 KB "
        "instruction segment in the PSRAM, default 0)",
    )
    sub_v4.add_argument("--entry", help="Entry point (default: the load address)")

    # two-stage boot
    subparsers.add_parser("sd-info", help="SD card, FAT32 and STAGE2 files (first stage's i)")
    sub_run = subparsers.add_parser("run", help="RAM-boot a second stage (v4 file) through the first stage")
    sub_run.add_argument("file", help="v4 file")
    sub_inst = subparsers.add_parser(
        "install-stage2", help="Install a second stage (v4 file) as \\VUX9K\\STAGE2.BIN and boot it"
    )
    sub_inst.add_argument("file", help="v4 file (it is RAM-booted, then installs itself)")
    sub_ls = subparsers.add_parser("ls", help="List a directory on the card (second stage's ls)")
    sub_ls.add_argument("path", nargs="?", default="", help="Directory or file (default: the root)")
    sub_put = subparsers.add_parser("put", help="Write a file to the card (second stage's put)")
    sub_put.add_argument("file", help="File to send")
    sub_put.add_argument("path", help="Where on the card (8.3 names, e.g. VUX9K/APPS/HELLO.BIN)")
    sub_runapp = subparsers.add_parser("run-app", help="Start a v4 file on the card (second stage's run)")
    sub_runapp.add_argument("path", help="v4 file on the card (e.g. VUX9K/APPS/HELLO.BIN)")
    sub_runapp.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds for the load, to the [RUN] line (default: 120; ~200-260 cycles a byte: BC.BIN takes ~3 s)",
    )

    args = parser.parse_args()

    if args.command == "mkv4":
        cmd_mkv4(args)
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
    elif args.command == "sd-info":
        cmd_sd_info(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "install-stage2":
        cmd_install_stage2(args)
    elif args.command == "ls":
        cmd_ls(args)
    elif args.command == "put":
        cmd_put(args)
    elif args.command == "run-app":
        cmd_run_app(args)


if __name__ == "__main__":
    main()
