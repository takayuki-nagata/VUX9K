#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Merge Boot Manager binary and Resident Loader binary into a 5120-word (20KB) hex file.
- Words 0..4607   (18 KB): Boot Manager (or NOP if empty)
- Words 4608..5119 (2 KB): Resident Loader
"""

import sys
import struct
import os

NOP = 0x00000013 # RISC-V addi x0, x0, 0

def merge_hex(bm_path, rl_path, out_hex_path):
    bm_data = b""
    if bm_path and os.path.exists(bm_path):
        with open(bm_path, "rb") as f:
            bm_data = f.read()

    rl_data = b""
    if rl_path and os.path.exists(rl_path):
        with open(rl_path, "rb") as f:
            rl_data = f.read()

    # If rl_data was extracted with absolute offset from 0, slice at 0x4800 (18432 bytes)
    if len(rl_data) >= 0x4800:
        rl_data = rl_data[0x4800:]

    # Total 5120 words (20 KB)
    words = [NOP] * 5120

    # Fill Boot Manager words (0..4607)
    for i in range(0, min(len(bm_data), 4608 * 4), 4):
        chunk = bm_data[i:i+4]
        if len(chunk) < 4:
            chunk = chunk + b'\x00' * (4 - len(chunk))
        words[i // 4] = struct.unpack('<I', chunk)[0]

    # Fill Resident Loader words (4608..5119)
    for i in range(0, min(len(rl_data), 512 * 4), 4):
        chunk = rl_data[i:i+4]
        if len(chunk) < 4:
            chunk = chunk + b'\x00' * (4 - len(chunk))
        words[4608 + (i // 4)] = struct.unpack('<I', chunk)[0]

    with open(out_hex_path, "w") as f:
        for w in words:
            f.write(f"{w:08x}\n")

    print(f"Generated {out_hex_path} (5120 words / 20 KB)")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 merge_firmware_hex.py <boot_manager.bin> <resident_loader.bin> <output.hex>")
        sys.exit(1)
    merge_hex(sys.argv[1], sys.argv[2], sys.argv[3])
