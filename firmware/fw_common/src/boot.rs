// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The first stage's part that needs no MMIO: where the second stage loads, the RAM
//! boot's receiver (a VUX9 v4 file over transfer v2, straight to its load address) and
//! the text of what keeps a second stage from starting.

use crate::fat::stage2::{BootError, Window};
use crate::fat::FatError;
use crate::fmt::Sink;
use crate::header::{ImageError, ImageHeader, HEADER_LEN, MODE_RISCV};
use crate::map;
use crate::upload::UploadSink;

/// Where a second stage may load (map::STAGE2_BASE, the top 1 MB of the PSRAM).
pub const STAGE2_WINDOW: Window = Window {
    base: map::STAGE2_BASE as u32,
    len: map::STAGE2_BYTES as u32,
};

/// The longest file a RAM boot takes: a header and a full window.
pub const RAM_BOOT_MAX: u32 = HEADER_LEN as u32 + map::STAGE2_BYTES as u32;

/// The first stage starts RV32 code only.
pub fn check_stage2(h: &ImageHeader) -> Result<(), BootError> {
    if h.mode != MODE_RISCV {
        return Err(BootError::Image(ImageError::BadMode(h.mode)));
    }
    Ok(())
}

/// Transfer v2's sink for a RAM boot: the first block's header is checked against the
/// window (and must be RV32) before anything is stored, the payload goes to `write`
/// (address, bytes) at its load address, and the payload's CRC is checked at the end.
/// After `upload_v2` succeeds, `header()` is what to start.
pub struct RamBoot<'a> {
    window: Window,
    write: &'a mut dyn FnMut(u32, &[u8]),
    len: u32,
    header: Option<ImageHeader>,
    /// Where the next payload byte goes.
    at: u32,
    crc: u32,
    /// finish(true) found the payload's CRC.
    done: bool,
    /// Why the transfer was refused, once it has been.
    pub error: Option<BootError>,
}

impl<'a> RamBoot<'a> {
    pub fn new(window: Window, write: &'a mut dyn FnMut(u32, &[u8])) -> Self {
        RamBoot {
            window,
            write,
            len: 0,
            header: None,
            at: 0,
            crc: 0xFFFF_FFFF,
            done: false,
            error: None,
        }
    }

    /// The checked header, once every byte has arrived with the header's CRC.
    pub fn header(&self) -> Option<ImageHeader> {
        if self.done {
            self.header
        } else {
            None
        }
    }

    fn fail(&mut self, e: BootError) -> bool {
        self.error = Some(e);
        false
    }
}

impl UploadSink for RamBoot<'_> {
    fn begin(&mut self, len: u32) -> bool {
        self.len = len;
        if len <= HEADER_LEN as u32 {
            return self.fail(BootError::NotV4);
        }
        true
    }

    fn block(&mut self, index: u32, data: &[u8]) -> bool {
        let mut payload = data;
        if index == 0 {
            // A block is 512 bytes (or the whole file, longer than the header)
            let Some(h) = ImageHeader::parse(data) else {
                return self.fail(BootError::NotV4);
            };
            if h.size as u64 + HEADER_LEN as u64 != self.len as u64 {
                return self.fail(BootError::SizeMismatch);
            }
            if let Err(e) = h.check(self.window.base, self.window.len) {
                return self.fail(BootError::Image(e));
            }
            if let Err(e) = check_stage2(&h) {
                return self.fail(e);
            }
            self.header = Some(h);
            self.at = h.load_addr;
            payload = &data[HEADER_LEN..];
        }
        // (upload_v2 sends no block after one that failed: there is a header)
        (self.write)(self.at, payload);
        self.crc = crate::crc32_update(payload, self.crc);
        self.at += payload.len() as u32;
        true
    }

    fn finish(&mut self, ok: bool) -> bool {
        if !ok {
            return true;
        }
        match self.header {
            Some(h) if self.crc ^ 0xFFFF_FFFF == h.crc32 => {
                self.done = true;
                true
            }
            _ => self.fail(BootError::Crc),
        }
    }
}

/// The number after a `put_boot_error` text.
enum Num {
    No,
    Dec(u32),
    Hex(u32),
}

/// Print why a second stage can't start, on one line without its end.
pub fn put_boot_error(out: &mut impl Sink, e: BootError) {
    let (what, n) = match e {
        BootError::Fat(f) => match f {
            FatError::Io(lba) => ("card error at LBA ", Num::Dec(lba)),
            FatError::NoSignature => ("no 55AA signature (MBR or boot sector)", Num::No),
            FatError::NoPartition => ("no partition", Num::No),
            FatError::NotFat32 => ("not FAT32 (reformat the card as FAT32)", Num::No),
            FatError::BadBpb => ("bad FAT32 boot sector", Num::No),
            FatError::BadChain(c) => ("bad cluster chain at cluster ", Num::Dec(c)),
            FatError::BadName => ("bad name", Num::No),
            FatError::NotFound => ("not found", Num::No),
            FatError::WrongType => ("not a file", Num::No),
            FatError::TooBig => ("too big", Num::No),
            FatError::Exists => ("exists", Num::No),
            FatError::DiskFull => ("card full", Num::No),
            FatError::Verify => ("read back differently", Num::No),
        },
        BootError::NotV4 => ("not a VUX9 v4 file", Num::No),
        BootError::SizeMismatch => ("file size is not the header's", Num::No),
        BootError::Image(i) => match i {
            ImageError::BadMode(m) => ("not RV32, mode ", Num::Dec(m)),
            ImageError::Empty => ("empty", Num::No),
            ImageError::OutOfWindow => ("loads outside 80700000-807FFFFF", Num::No),
            ImageError::BadEntry(a) => ("bad entry point ", Num::Hex(a)),
        },
        BootError::Crc => ("CRC mismatch", Num::No),
    };
    out.put_str(what);
    match n {
        Num::No => {}
        Num::Dec(n) => out.put_dec(n),
        Num::Hex(a) => out.put_hex(a),
    }
}
