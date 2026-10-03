// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD slot layout and the 64-byte VUX9 v3 boot header (README "SD card layout").
//!
//! tools/vux_tool.py has the same header (`SlotHeader`, the constants below) for the
//! tool that writes the slots. tests/vux9_vectors/ holds headers it made, and both
//! sides check them (tests/host.rs, sim/emu/test_vux9_header.py, which also compares
//! the constants): change both sides and regenerate the vectors together.

pub const VUX_MAGIC: u32 = 0x5655_5839; // "VUX9"
pub const HEADER_VERSION: u16 = 3;
pub const HEADER_LEN: usize = 64;
pub const SECTOR: usize = 512;
/// Payload bytes in a slot's first sector, after the header.
pub const FIRST_SECTOR_PAYLOAD: usize = SECTOR - HEADER_LEN;
pub const FLAG_VALID: u16 = 1;
/// Set by vux_tool on slot 0 (the Boot Manager's update image); informational only.
pub const FLAG_SYSTEM: u16 = 4;
/// `mode` values
pub const MODE_HACK: u32 = 0;
pub const MODE_RISCV: u32 = 1;

/// Byte offsets of the header fields (all little-endian)
pub const OFF_MAGIC: usize = 0;
pub const OFF_HEADER_VERSION: usize = 4; // u16
pub const OFF_FLAGS: usize = 6; // u16
pub const OFF_MODE: usize = 8;
pub const OFF_SIZE: usize = 12;
pub const OFF_LOAD_ADDR: usize = 16;
pub const OFF_ENTRY_POINT: usize = 20;
pub const OFF_CRC32: usize = 24;
pub const OFF_VERSION: usize = 28;
pub const OFF_NAME: usize = 32; // 32 bytes, NUL-padded

/// First SD sector (LBA) of slot 0-9: 64 sectors (32 KB) each from LBA 64.
pub fn slot_sector(slot: u32) -> u32 {
    64 + (slot << 6)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SlotHeader {
    pub header_version: u16,
    pub flags: u16,
    /// MODE_RISCV or MODE_HACK
    pub mode: u32,
    pub size: u32,
    pub load_addr: u32,
    pub entry_point: u32,
    pub crc32: u32,
    pub version: u32,
    pub name: [u8; 32],
}

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

impl SlotHeader {
    /// The header at the start of a slot's first sector, if it is a valid VUX9 v3
    /// header (magic, version 3, valid flag); None for an empty or foreign slot.
    pub fn parse(sector: &[u8]) -> Option<SlotHeader> {
        if sector.len() < HEADER_LEN || le32(sector, OFF_MAGIC) != VUX_MAGIC {
            return None;
        }
        let le16 = |at: usize| u16::from_le_bytes([sector[at], sector[at + 1]]);
        let header_version = le16(OFF_HEADER_VERSION);
        let flags = le16(OFF_FLAGS);
        if header_version != HEADER_VERSION || (flags & FLAG_VALID) == 0 {
            return None;
        }
        let mut name = [0u8; 32];
        name.copy_from_slice(&sector[OFF_NAME..HEADER_LEN]);
        Some(SlotHeader {
            header_version,
            flags,
            mode: le32(sector, OFF_MODE),
            size: le32(sector, OFF_SIZE),
            load_addr: le32(sector, OFF_LOAD_ADDR),
            entry_point: le32(sector, OFF_ENTRY_POINT),
            crc32: le32(sector, OFF_CRC32),
            version: le32(sector, OFF_VERSION),
            name,
        })
    }

    /// Length of the printable (0x20-0x7E) prefix of the name, up to its NUL.
    pub fn name_len(&self) -> usize {
        self.name
            .iter()
            .position(|&c| !(0x20..=0x7E).contains(&c))
            .unwrap_or(self.name.len())
    }

    pub fn is_riscv(&self) -> bool {
        self.mode == MODE_RISCV
    }
}
