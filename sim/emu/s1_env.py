# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Shared setup for the two-stage boot's tests on the emulator: the first stage
(firmware/stage1) and the provisional second stage (firmware/stage2) on FAT32 cards."""

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
# The banner's last line: the key window starts after it
BANNER_END = b"3 MHz\n"


def stage2(version=1, **kw) -> bytes:
    """The provisional second stage as a v4 file (as `make stage2` builds it)."""
    with open(STAGE2_RAW, "rb") as f:
        raw = f.read()
    kw.setdefault("load_addr", STAGE2_BASE)
    data, _ = vux_tool.build_vux9_v4_image(raw, name="VUX9K stage 2 (provisional)", version=version, **kw)
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
    """Power on with a card holding \\VUX9K\\STAGE2.BIN (the provisional second stage
    unless given) and run to its prompt; returns (soc, output)."""
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
