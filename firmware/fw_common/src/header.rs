// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD slot layout and the 64-byte VUX9 v3 boot header (tools/vux_tool.py,
//! build_vux9_image; README "SD card layout").

pub const VUX_MAGIC: u32 = 0x5655_5839; // "VUX9"
pub const HEADER_VERSION: u16 = 3;
pub const HEADER_LEN: usize = 64;
/// Payload bytes in a slot's first sector, after the header.
pub const FIRST_SECTOR_PAYLOAD: usize = 512 - HEADER_LEN;
pub const FLAG_VALID: u16 = 1;

/// First SD sector (LBA) of slot 0-9: 64 sectors (32 KB) each from LBA 64.
pub fn slot_sector(slot: u32) -> u32 {
    64 + (slot << 6)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SlotHeader {
    pub header_version: u16,
    pub flags: u16,
    /// 1 = RV32I, 0 = Hack
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
        if sector.len() < HEADER_LEN || le32(sector, 0) != VUX_MAGIC {
            return None;
        }
        let header_version = u16::from_le_bytes([sector[4], sector[5]]);
        let flags = u16::from_le_bytes([sector[6], sector[7]]);
        if header_version != HEADER_VERSION || (flags & FLAG_VALID) == 0 {
            return None;
        }
        let mut name = [0u8; 32];
        name.copy_from_slice(&sector[32..64]);
        Some(SlotHeader {
            header_version,
            flags,
            mode: le32(sector, 8),
            size: le32(sector, 12),
            load_addr: le32(sector, 16),
            entry_point: le32(sector, 20),
            crc32: le32(sector, 24),
            version: le32(sector, 28),
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
        self.mode == 1
    }
}
