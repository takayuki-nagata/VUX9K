#!/usr/bin/env python3
import struct

# Create a 64-byte sample Hack / RISC-V test binary
# 16 instructions: RISC-V NOPs / Gpio write instructions
data = bytearray()
for i in range(16):
    data.extend(struct.pack('<I', 0x00000013)) # NOP

with open("test_payload.bin", "wb") as f:
    f.write(data)
print("Created test_payload.bin (64 bytes)")
