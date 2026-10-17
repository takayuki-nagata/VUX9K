#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Tang Nano 9K ELF to binary image extractor.

Without --base: the boot ROM's image, every segment whose load address (p_paddr) is in
region 0 (below 0x1000_0000), from 0; a .data that runs elsewhere (the PSRAM) is in it at its
load address and copied out at startup. With --base ADDR, the image is instead everything
loaded at ADDR and above (a program linked into the PSRAM, such as the second stage), from
ADDR on.
"""

import struct
import sys


def elf2bin(elf_path, bin_path, base=None):
    with open(elf_path, "rb") as f:
        data = f.read()

    if data[:4] != b"\x7fELF":
        print(f"Error: {elf_path} is not a valid ELF file.")
        sys.exit(1)

    e_phoff = struct.unpack("<I", data[0x1C:0x20])[0]
    e_phentsize = struct.unpack("<H", data[0x2A:0x2C])[0]
    e_phnum = struct.unpack("<H", data[0x2C:0x2E])[0]

    image = bytearray()
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack(
            "<IIIIIIII", data[off : off + 32]
        )
        if p_type != 1 or p_filesz == 0:
            continue
        if base is not None:
            if p_paddr < base:
                continue
            rel_offset = p_paddr - base
        elif p_paddr < 0x1000_0000:
            rel_offset = p_paddr
        else:
            continue
        if len(image) < rel_offset + p_filesz:
            image.extend(b"\x00" * (rel_offset + p_filesz - len(image)))
        image[rel_offset : rel_offset + p_filesz] = data[p_offset : p_offset + p_filesz]

    with open(bin_path, "wb") as f:
        f.write(image)
    print(f"Successfully extracted {len(image)} bytes image -> {bin_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    base = None
    if args[:1] == ["--base"] and len(args) > 1:
        base = int(args[1], 0)
        args = args[2:]
    if len(args) != 2:
        print("Usage: python3 elf2bin.py [--base ADDR] <elf_file> <bin_file>")
        sys.exit(1)
    elf2bin(args[0], args[1], base)
