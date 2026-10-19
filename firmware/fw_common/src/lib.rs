// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The firmware's hardware-independent logic: the SoC's address map, the VUX9 v4 file
//! header, CRC32, transfer v2 (`upload`), FAT32 (`fat`) with the second stage's files
//! (`fat::stage2`), the first stage's RAM boot (`boot`), the second stage's launcher
//! (`launch`) and command line (`shell`), and number formatting. No MMIO
//! here (the drivers pass themselves in as traits), so all of it is tested on the host:
//! `cargo test -p fw_common --target x86_64-unknown-linux-gnu --features fat-full`
//! (make test-fw-host).

#![no_std]

pub mod boot;
pub mod fat;
pub mod fmt;
pub mod header;
// The second stage's (with the file manager); not built for the first stage, whose code the
// module alone changed
#[cfg(feature = "fat-full")]
pub mod launch;
pub mod map;
pub mod shell;
pub mod upload;

/// CRC-32 (IEEE, reflected, poly 0xEDB88320) over `data`, continuing from `crc`.
/// Start with 0xFFFF_FFFF and invert the result, as zlib/binascii.crc32 do.
/// Bit by bit: the first stage's (small, and fast enough from the ROM).
#[cfg(not(feature = "fat-full"))]
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

/// The CRC-32 of each 4-bit value, for `crc32_update` a nibble at a time.
#[cfg(feature = "fat-full")]
const CRC_NIBBLES: [u32; 16] = {
    let mut t = [0u32; 16];
    let mut i = 0;
    while i < 16 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 4 {
            c = if (c & 1) != 0 {
                (c >> 1) ^ 0xEDB8_8320
            } else {
                c >> 1
            };
            k += 1;
        }
        t[i] = c;
        i += 1;
    }
    t
};

/// CRC-32 as above, a nibble at a time from a 64-byte table: the second stage's
/// (feature `fat-full`). From the PSRAM before its caches, every fetch waiting ~35 cycles,
/// the bitwise loop took ~1,800 cycles a byte of every file it loaded.
#[cfg(feature = "fat-full")]
pub fn crc32_update(data: &[u8], mut crc: u32) -> u32 {
    for &b in data {
        crc ^= b as u32;
        crc = (crc >> 4) ^ CRC_NIBBLES[(crc & 0xF) as usize];
        crc = (crc >> 4) ^ CRC_NIBBLES[(crc & 0xF) as usize];
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
