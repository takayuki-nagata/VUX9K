#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Tang Nano 9K Harvard Architecture ELF to Binary Image Extractor
Extracts instruction ROM image (p_paddr < 0x20000000) from RISC-V ELF.
"""

import struct
import sys


def elf2bin(elf_path, bin_path, dram_outdir=None):
    with open(elf_path, "rb") as f:
        data = f.read()

    if data[:4] != b"\x7fELF":
        print(f"Error: {elf_path} is not a valid ELF file.")
        sys.exit(1)

    e_phoff = struct.unpack("<I", data[0x1C:0x20])[0]
    e_phentsize = struct.unpack("<H", data[0x2A:0x2C])[0]
    e_phnum = struct.unpack("<H", data[0x2C:0x2E])[0]

    rom_data = bytearray()
    ram_data = bytearray(8192)  # 8 KB D-RAM

    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack(
            "<IIIIIIII", data[off : off + 32]
        )

        if p_type == 1 and p_filesz > 0:
            if p_paddr < 0x20000000 and p_vaddr < 0x20000000:
                rel_offset = p_paddr
                if len(rom_data) < rel_offset + p_filesz:
                    rom_data.extend(b"\x00" * (rel_offset + p_filesz - len(rom_data)))
                rom_data[rel_offset : rel_offset + p_filesz] = data[p_offset : p_offset + p_filesz]
            elif p_vaddr >= 0x20000000:
                ram_offset = p_vaddr - 0x20000000
                if ram_offset + p_filesz <= len(ram_data):
                    ram_data[ram_offset : ram_offset + p_filesz] = data[p_offset : p_offset + p_filesz]

    with open(bin_path, "wb") as f:
        f.write(rom_data)
    print(f"Successfully extracted {len(rom_data)} bytes ROM image -> {bin_path}")

    if dram_outdir is None:
        return

    # Write D-RAM byte-lane hex files (d_mem0..3)
    d0_lines = []
    d1_lines = []
    d2_lines = []
    d3_lines = []
    for w_idx in range(2048):
        b0 = ram_data[w_idx * 4 + 0]
        b1 = ram_data[w_idx * 4 + 1]
        b2 = ram_data[w_idx * 4 + 2]
        b3 = ram_data[w_idx * 4 + 3]
        d0_lines.append(f"{b0:02x}\n")
        d1_lines.append(f"{b1:02x}\n")
        d2_lines.append(f"{b2:02x}\n")
        d3_lines.append(f"{b3:02x}\n")

    import os

    os.makedirs(dram_outdir, exist_ok=True)
    for idx, lines in enumerate([d0_lines, d1_lines, d2_lines, d3_lines]):
        out_path = os.path.join(dram_outdir, f"firmware_d{idx}.hex")
        with open(out_path, "w") as df:
            df.writelines(lines)
    print(f"Successfully generated D-RAM preloads: {dram_outdir}/firmware_d0..3.hex")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 elf2bin.py <elf_file> <bin_file> [dram_outdir]")
        sys.exit(1)
    elf2bin(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
