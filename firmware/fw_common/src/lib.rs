// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The firmware's hardware-independent logic: the SoC's address map, the VUX9 v4 file
//! header, CRC32, transfer v2 (`upload`), FAT32 (`fat`) with the second stage's files
//! (`fat::stage2`), the first stage's RAM boot (`boot`), and number formatting. No MMIO
//! here (the drivers pass themselves in as traits), so all of it is tested on the host:
//! `cargo test -p fw_common --target x86_64-unknown-linux-gnu --features fat-full`
//! (make test-fw-host).

#![no_std]

pub mod boot;
pub mod fat;
pub mod fmt;
pub mod header;
pub mod map;
pub mod upload;

/// CRC-32 (IEEE, reflected, poly 0xEDB88320) over `data`, continuing from `crc`.
/// Start with 0xFFFF_FFFF and invert the result, as zlib/binascii.crc32 do.
pub fn crc32_update(data: &[u8], mut crc: u32) -> u32 {
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if (crc & 1) != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    crc
}

/// a * b by shift-and-add: RV32I has no multiplier, and this keeps libgcc's
/// __mulsi3 out of the firmware.
#[inline(never)]
pub fn mul_u32(mut a: u32, mut b: u32) -> u32 {
    let mut res = 0u32;
    while b != 0 {
        if (b & 1) != 0 {
            res = res.wrapping_add(a);
        }
        a <<= 1;
        b >>= 1;
    }
    res
}
