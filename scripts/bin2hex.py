#!/usr/bin/env python3
"""
Binary to Hex Word Converter for VHDL Memory Preloading
Reads binary file and outputs 8-digit hex words per line.
"""

import sys
import struct

def bin2hex(bin_path, hex_path):
    with open(bin_path, 'rb') as f:
        data = f.read()

    lines = []
    for i in range(0, len(data), 4):
        chunk = data[i:i+4]
        if len(chunk) < 4:
            chunk = chunk + b'\x00' * (4 - len(chunk))
        word = struct.unpack('<I', chunk)[0]
        lines.append(f"{word:08x}\n")

    with open(hex_path, 'w') as f:
        f.writelines(lines)
    print(f"Converted {bin_path} -> {hex_path} ({len(lines)} words)")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 bin2hex.py <bin_file> <hex_file>")
        sys.exit(1)
    bin2hex(sys.argv[1], sys.argv[2])
