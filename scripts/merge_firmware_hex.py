#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Turn a boot ROM image (the first stage, hw_test) into the 4096-word (16 KB) hex file that
soc_rom's $readmemh loads, the rest filled with NOPs.
"""

import struct
import sys

NOP = 0x00000013  # RISC-V addi x0, x0, 0
WORDS = 4096


def merge_hex(bin_path, out_hex_path):
    with open(bin_path, "rb") as f:
        data = f.read()
    if len(data) > WORDS * 4:
        sys.exit(f"{bin_path}: {len(data)} bytes, more than the {WORDS * 4} that fit")
    data += b"\x00" * (-len(data) % 4)

    words = [NOP] * WORDS
    for i in range(0, len(data), 4):
        words[i // 4] = struct.unpack("<I", data[i : i + 4])[0]

    with open(out_hex_path, "w") as f:
        for w in words:
            f.write(f"{w:08x}\n")

    print(f"Generated {out_hex_path} ({WORDS} words / 16 KB, {len(data)} bytes used)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 merge_firmware_hex.py <image.bin> <output.hex>")
        sys.exit(1)
    merge_hex(sys.argv[1], sys.argv[2])
