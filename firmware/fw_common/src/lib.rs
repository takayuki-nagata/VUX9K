// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The Boot Manager's hardware-independent logic: the SD slot layout and VUX9 boot
//! header, CRC32, the self-update checks, the Boot Manager -> Resident Loader
//! mailbox, and number formatting. No MMIO here, so all of it is tested on the host:
//! `cargo test -p fw_common --target x86_64-unknown-linux-gnu` (make test-fw-host).
//!
//! The Resident Loader keeps its own copies of the few pieces it needs: it must fit
//! in 2 KB (make firmware-size).

#![no_std]

pub mod fmt;
pub mod header;
pub mod mailbox;
pub mod update;

pub use header::{slot_sector, SlotHeader};

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
/// __mulsi3 out of the Boot Manager.
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
