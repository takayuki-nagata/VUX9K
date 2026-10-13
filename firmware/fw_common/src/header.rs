// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The 64-byte VUX9 v4 header of the files the two-stage boot loads (`ImageHeader`;
//! README "VUX9 file header").
//!
//! tools/vux_tool.py has the same header (`ImageHeader`, the constants below) for the
//! tool that writes it. tests/vux9_v4_vectors/ holds headers it made, and both sides
//! check them (tests/host.rs, sim/emu/test_vux9_header.py, which also compares the
//! constants): change both sides and regenerate the vectors together.

pub const VUX_MAGIC: u32 = 0x5655_5839; // "VUX9"
pub const HEADER_LEN: usize = 64;
pub const SECTOR: usize = 512;
/// `mode` values
pub const MODE_HACK: u32 = 0;
pub const MODE_RISCV: u32 = 1;

/// Byte offsets of the header fields (all little-endian)
pub const OFF_MAGIC: usize = 0;
pub const OFF_HEADER_VERSION: usize = 4; // u16
pub const OFF_MODE: usize = 8;
pub const OFF_SIZE: usize = 12;
pub const OFF_LOAD_ADDR: usize = 16;
pub const OFF_ENTRY_POINT: usize = 20;
pub const OFF_CRC32: usize = 24;
pub const OFF_VERSION: usize = 28;
pub const OFF_NAME: usize = 32; // 32 bytes, NUL-padded

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// Length of the printable (0x20-0x7E) prefix of a name, up to its NUL.
fn printable_len(name: &[u8; 32]) -> usize {
    name.iter()
        .position(|&c| !(0x20..=0x7E).contains(&c))
        .unwrap_or(name.len())
}

/// v4: the header of a file (header, then the payload; no padding). Same 64-byte
/// layout and offsets as the retired v3 slot header, whose flags are reserved and must be 0.
pub const HEADER_VERSION_V4: u16 = 4;
/// u16, 0 (v3's flags).
pub const OFF_RESERVED: usize = 6;

/// A VUX9 v4 header: what to load where, and how to check it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ImageHeader {
    /// MODE_RISCV or MODE_HACK
    pub mode: u32,
    /// Payload bytes after the header.
    pub size: u32,
    pub load_addr: u32,
    pub entry_point: u32,
    /// CRC-32 of the payload (`crate::crc32_update`, inverted).
    pub crc32: u32,
    pub version: u32,
    pub name: [u8; 32],
}

/// Why a v4 header can't be loaded into a window (`ImageHeader::check`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImageError {
    /// Neither MODE_RISCV nor MODE_HACK.
    BadMode(u32),
    /// Size 0.
    Empty,
    /// `load_addr..load_addr + size` is not inside the window.
    OutOfWindow,
    /// RV32: entry outside the loaded bytes or not word-aligned.
    BadEntry(u32),
}

impl ImageHeader {
    /// The header at the start of `b`, if it is a VUX9 v4 header (magic, version 4,
    /// reserved 0); None otherwise.
    pub fn parse(b: &[u8]) -> Option<ImageHeader> {
        if b.len() < HEADER_LEN
            || le32(b, OFF_MAGIC) != VUX_MAGIC
            || u16::from_le_bytes([b[OFF_HEADER_VERSION], b[OFF_HEADER_VERSION + 1]])
                != HEADER_VERSION_V4
            || u16::from_le_bytes([b[OFF_RESERVED], b[OFF_RESERVED + 1]]) != 0
        {
            return None;
        }
        let mut name = [0u8; 32];
        name.copy_from_slice(&b[OFF_NAME..OFF_NAME + 32]);
        Some(ImageHeader {
            mode: le32(b, OFF_MODE),
            size: le32(b, OFF_SIZE),
            load_addr: le32(b, OFF_LOAD_ADDR),
            entry_point: le32(b, OFF_ENTRY_POINT),
            crc32: le32(b, OFF_CRC32),
            version: le32(b, OFF_VERSION),
            name,
        })
    }

    /// Whether the payload fits `base..base + len` (the caller's load window) and, for
    /// RV32, starts at a word-aligned entry inside it.
    pub fn check(&self, base: u32, len: u32) -> Result<(), ImageError> {
        if self.mode != MODE_RISCV && self.mode != MODE_HACK {
            return Err(ImageError::BadMode(self.mode));
        }
        if self.size == 0 {
            return Err(ImageError::Empty);
        }
        if self.load_addr < base || self.size > len || self.load_addr - base > len - self.size {
            return Err(ImageError::OutOfWindow);
        }
        let e = self.entry_point;
        if self.mode == MODE_RISCV
            && (e < self.load_addr || e - self.load_addr >= self.size || (e & 3) != 0)
        {
            return Err(ImageError::BadEntry(e));
        }
        Ok(())
    }

    /// Whether `payload` (all `size` bytes of it) has the header's CRC.
    pub fn crc_matches(&self, payload: &[u8]) -> bool {
        payload.len() == self.size as usize
            && crate::crc32_update(payload, 0xFFFF_FFFF) ^ 0xFFFF_FFFF == self.crc32
    }

    /// Length of the printable (0x20-0x7E) prefix of the name, up to its NUL.
    pub fn name_len(&self) -> usize {
        printable_len(&self.name)
    }
}
