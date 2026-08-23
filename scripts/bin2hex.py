#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Binary to Hex Word Converter for VHDL Memory Preloading
Reads binary file and outputs 8-digit hex words per line.
"""

import sys
import struct
import argparse

def bin2hex(bin_path, hex_path, is_hack=None):
    with open(bin_path, 'rb') as f:
        data = f.read()

    # Auto-detect Hack mode if not specified: Hack starts with A-instruction where bits [15] == 0,
    # whereas RISC-V 32-bit little-endian instructions have [1:0] == 2'b11.
    if is_hack is None and len(data) >= 4:
        rv_opcode = data[0] & 0x7F
        rv_valid = (data[0] & 3 == 3) and rv_opcode in (0x33, 0x13, 0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73)
        is_hack = not rv_valid

    lines = []
    if is_hack:
        # Hack big-endian 16-bit instructions packed into 32-bit words (instr0 in lower 16 bits, instr1 in upper 16 bits)
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            instr0 = (chunk[0] << 8) | chunk[1]
            instr1 = (chunk[2] << 8) | chunk[3]
            word = (instr1 << 16) | instr0
            lines.append(f"{word:08x}\n")
    else:
        # RISC-V 32-bit little-endian words
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            word = struct.unpack('<I', chunk)[0]
            lines.append(f"{word:08x}\n")

    with open(hex_path, 'w') as f:
        f.writelines(lines)
    print(f"Converted {bin_path} -> {hex_path} ({len(lines)} words, {'Hack 16-bit' if is_hack else 'RISC-V 32-bit'})")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert .bin to .hex for SoC memory preloading")
    parser.add_argument("bin_file", help="Input binary file")
    parser.add_argument("hex_file", help="Output hex file")
    parser.add_argument("--hack", action="store_true", help="Force Hack 16-bit big-endian packing")
    parser.add_argument("--riscv", action="store_true", help="Force RISC-V 32-bit little-endian packing")
    args = parser.parse_args()

    is_hack = True if args.hack else (False if args.riscv else None)
    bin2hex(args.bin_file, args.hex_file, is_hack=is_hack)
