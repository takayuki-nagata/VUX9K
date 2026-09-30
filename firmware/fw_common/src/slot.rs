// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Reading a slot's payload back from the card: its CRC32, as the header records it.

use crate::crc32_update;
use crate::header::{FIRST_SECTOR_PAYLOAD, HEADER_LEN, SECTOR};

/// CRC32 of a `size`-byte payload whose slot starts at `first_sector`. `buf` holds
/// that sector (header and the payload's start) and is reused for the rest, which
/// `read(sector, buf)` fetches. `Err(sector)` names the first sector `read` failed on.
pub fn payload_crc(
    size: u32,
    first_sector: u32,
    buf: &mut [u8; SECTOR],
    mut read: impl FnMut(u32, &mut [u8; SECTOR]) -> bool,
) -> Result<u32, u32> {
    let size = size as usize;
    let first = if size < FIRST_SECTOR_PAYLOAD {
        size
    } else {
        FIRST_SECTOR_PAYLOAD
    };
    let mut crc = crc32_update(&buf[HEADER_LEN..HEADER_LEN + first], 0xFFFF_FFFF);
    let mut remaining = size - first;
    let mut sector = first_sector + 1;
    while remaining > 0 {
        if !read(sector, buf) {
            return Err(sector);
        }
        let n = if remaining < SECTOR {
            remaining
        } else {
            SECTOR
        };
        crc = crc32_update(&buf[..n], crc);
        remaining -= n;
        sector += 1;
    }
    Ok(crc ^ 0xFFFF_FFFF)
}
