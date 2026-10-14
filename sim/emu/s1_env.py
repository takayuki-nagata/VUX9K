# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Shared setup for the two-stage boot's tests on the emulator: the first stage
(firmware/stage1) and the second stage (firmware/stage2) on FAT32 cards."""

import os
import re
import struct

from vux9k import (
    FIRMWARE_DIR,
    REPO_ROOT,
    UART_BIT,
    new_soc,
    preload_firmware,
    vux_tool,
    wait_for,
)

STAGE1_DIR = FIRMWARE_DIR
STAGE2_RAW = os.path.join(FIRMWARE_DIR, "stage2", "stage2.bin")
S1_PROMPT = b"s1> "
S2_PROMPT = b"s2> "
STAGE2_BASE = 0x8070_0000
FAT_LBA = vux_tool.FAT_PART_LBA
# Power-on to the second stage's prompt, or to the first stage's after a failed boot: the
# banner, the key window (100 ms), card init and a 16 KB second stage at ~250 cycles a byte
BOOT_CYCLES = 40_000_000
with open(os.path.join(REPO_ROOT, "firmware", "stage1", "src", "main.rs")) as _f:
    _m = re.search(r"const STAGE1_VERSION: u32 = (\d+);", _f.read())
    assert _m, "STAGE1_VERSION not found in main.rs"
    STAGE1_VERSION = int(_m.group(1))
BANNER = f"VUX9K Stage 1 (v{STAGE1_VERSION})"
with open(os.path.join(REPO_ROOT, "firmware", "stage2", "src", "main.rs")) as _f:
    _m = re.search(r"const STAGE2_VERSION: u32 = (\d+);", _f.read())
    assert _m, "STAGE2_VERSION not found in main.rs"
    STAGE2_VERSION = int(_m.group(1))
S2_BANNER = f"VUX9K Stage 2 (v{STAGE2_VERSION})"
# The second stage's v4 name (the Makefile's mkv4)
S2_NAME = "VUX9K stage 2"
# The banner's last line: the key window starts after it
BANNER_END = b"3 MHz\n"
# The demos as v4 files for \VUX9K\APPS\ (make apps)
APPS_DIR = os.path.join(FIRMWARE_DIR, "apps")
# `run` to the blank line after its [RUN] line: the second stage, running from the PSRAM,
# loads ~2,000-2,400 cycles a byte (ZEPHYR.BIN, 13 KB: ~27M)
RUN_LOAD_CYCLES = 40_000_000


def stage2(version=STAGE2_VERSION, **kw) -> bytes:
    """The second stage as a v4 file (as `make firmware` builds it)."""
    with open(STAGE2_RAW, "rb") as f:
        raw = f.read()
    kw.setdefault("load_addr", STAGE2_BASE)
    data, _ = vux_tool.build_vux9_v4_image(raw, name=S2_NAME, version=version, **kw)
    return data


def card(files=None, **kw) -> bytes:
    """A FAT32 card image holding files ({"VUX9K/STAGE2.BIN": bytes, ...})."""
    return vux_tool.build_fat32_image(files or {}, **kw)


def fixture(name: str) -> bytes:
    """A card image of firmware/fw_common/tests/fat_images/ (made with mkfs.fat and
    mtools: long names, directories over several clusters; sim/emu/test_fat_images.py)."""
    path = os.path.join(REPO_ROOT, "firmware", "fw_common", "tests", "fat_images", f"{name}.sparse")
    with open(path, "rb") as f:
        raw = f.read()
    assert raw[:8] == b"VUXSPRS1"
    (total,) = struct.unpack_from("<I", raw, 8)
    img = bytearray(total * 512)
    for at in range(12, len(raw), 4 + 512):
        (lba,) = struct.unpack_from("<I", raw, at)
        img[lba * 512 : (lba + 1) * 512] = raw[at + 4 : at + 4 + 512]
    return bytes(img)


def entry_at(img: bytes | bytearray, name: bytes) -> int:
    """Byte offset of the (8.3, space-padded) directory entry `name` in a card image."""
    at = FAT_LBA * 512
    while True:
        at = img.find(name, at)
        assert at >= 0, f"no entry {name!r}"
        if at % 32 == 0:
            return at
        at += 1


def start(image=None, *, files=None, card_present=True, sd_card=None, button=False):
    """The SoC with the first stage in I-RAM, right after power-on reset."""
    soc = new_soc()
    preload_firmware(soc, STAGE1_DIR)
    if card_present:
        if image is None:
            image = card(files)
        soc.sd_insert(image, **(sd_card or {}))
    soc.set_button(button)
    return soc


def boot(stage2_file=None, **kw):
    """Power on with a card holding \\VUX9K\\STAGE2.BIN (the second stage unless given)
    and run to its prompt; returns (soc, output)."""
    files = kw.pop("files", None) or {"VUX9K/STAGE2.BIN": stage2_file or stage2()}
    soc = start(files=files, **kw)
    return soc, wait_for(soc, S2_PROMPT, BOOT_CYCLES)


def to_prompt(soc) -> str:
    """Send a key right after the banner, so the first stage stays at its prompt;
    returns the output up to the prompt."""
    wait_for(soc, BANNER_END, BOOT_CYCLES)
    soc.uart_send(b"\r")
    return wait_for(soc, S1_PROMPT, BOOT_CYCLES)


def cmd(soc, text: str, until: bytes = S1_PROMPT, timeout_cycles: int = BOOT_CYCLES) -> str:
    """A command at a prompt (after a short pause, like a person); returns the reply."""
    soc.run(50 * UART_BIT)
    start_at = len(soc.uart_received())
    soc.uart_send(text.encode())
    resp = wait_for(soc, until, timeout_cycles=timeout_cycles, start=start_at)
    echo = f"[CMD:0x{ord(text[0]):02X}]"
    assert echo in resp, f"missing command echo {echo} for {text!r}: {resp!r}"
    return resp


def handoff(out: str):
    """a0, a1, a2 as the second stage printed them."""
    m = re.search(r"\[S2\] a0=0x([0-9A-F]{8}) a1=0x([0-9A-F]{8}) a2=0x([0-9A-F]{8})", out)
    assert m, out
    return tuple(int(g, 16) for g in m.groups())


def boot_s2(files=None, image=None, **kw):
    """Power on with a card holding the second stage as \\VUX9K\\STAGE2.BIN and `files`
    ({"DIR/NAME.EXT": bytes}), or the card `image` as it is (it must hold the second
    stage), and run to the second stage's prompt; returns (soc, output)."""
    if image is None:
        image = card({"VUX9K/STAGE2.BIN": stage2()} | (files or {}))
    soc = start(image, **kw)
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES)
    assert S2_BANNER in out, out
    return soc, out


def s2_cmd(soc, line: str, until: bytes = S2_PROMPT, timeout_cycles: int = BOOT_CYCLES) -> str:
    """A command line at the second stage's prompt (after a short pause, like a person);
    returns what it printed, without the echo of the line (and up to `until`, the next
    prompt by default, which is left out)."""
    soc.run(50 * UART_BIT)
    start_at = len(soc.uart_received())
    soc.uart_send(line.encode() + b"\r")
    resp = wait_for(soc, until, timeout_cycles=timeout_cycles, start=start_at)
    echo = line + "\n"
    assert resp.startswith(echo), f"missing echo of {line!r}: {resp!r}"
    resp = resp[len(echo) :]
    return resp[: -len(until)] if until == S2_PROMPT else resp


def app(name: str) -> bytes:
    """A demo's v4 file from APPS_DIR (e.g. "HELLO.BIN")."""
    path = os.path.join(APPS_DIR, name)
    assert os.path.exists(path), f"{path} missing; run `make apps`"
    with open(path, "rb") as f:
        return f.read()


def run_from_card(name: str, data: bytes | None = None, **kw):
    """Power on with a card holding the second stage and \\VUX9K\\APPS\\<name> (`data`,
    else the demo's file from APPS_DIR), run to the second stage's prompt and type `run
    VUX9K/APPS/<name>`, as on the board; returns (soc, mark), mark being where what the
    application prints starts in soc.uart_received() (after the [RUN] line and the blank
    line after it). kw goes to start()."""
    soc, _ = boot_s2({f"VUX9K/APPS/{name}": app(name) if data is None else data}, **kw)
    out = s2_cmd(soc, f"run VUX9K/APPS/{name}", until=b"\n\n", timeout_cycles=RUN_LOAD_CYCLES)
    assert out.startswith("[RUN] "), out
    return soc, len(soc.uart_received())  # (wait_for stops at the end of what it waited for)
