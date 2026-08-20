#!/usr/bin/env python3
"""
Tang Nano 9K Harvard Architecture ELF to Binary Image Extractor
Extracts instruction ROM image (p_paddr < 0x20000000) from RISC-V ELF.
"""

import sys
import struct

def elf2bin(elf_path, bin_path):
    with open(elf_path, 'rb') as f:
        data = f.read()

    if data[:4] != b'\x7fELF':
        print(f"Error: {elf_path} is not a valid ELF file.")
        sys.exit(1)

    e_phoff = struct.unpack('<I', data[0x1C:0x20])[0]
    e_phentsize = struct.unpack('<H', data[0x2A:0x2C])[0]
    e_phnum = struct.unpack('<H', data[0x2C:0x2E])[0]

    rom_data = bytearray()

    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack('<IIIIIIII', data[off:off+32])

        # Extract only loadable ROM segments (p_paddr < 0x20000000)
        if p_type == 1 and p_filesz > 0 and p_paddr < 0x20000000:
            rel_offset = p_paddr
            if len(rom_data) < rel_offset + p_filesz:
                rom_data.extend(b'\x00' * (rel_offset + p_filesz - len(rom_data)))
            rom_data[rel_offset:rel_offset+p_filesz] = data[p_offset:p_offset+p_filesz]

    with open(bin_path, 'wb') as f:
        f.write(rom_data)
    print(f"Successfully extracted {len(rom_data)} bytes ROM image -> {bin_path}")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 elf2bin.py <elf_file> <bin_file>")
        sys.exit(1)
    elf2bin(sys.argv[1], sys.argv[2])
