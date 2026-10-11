// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage on the card: `\VUX9K\STAGE2.BIN`, a VUX9 v4 file, with the previous
//! one kept as `STAGE2.OLD`. `select_boot` finds the file to start (`.BIN`, else `.OLD`);
//! `install` (feature `fat-write`) replaces it so that every point where power can fail
//! leaves one of the two bootable:
//!
//! 1. remove a `STAGE2.NEW` left over from an interrupted install;
//! 2. write `STAGE2.NEW` and read it back (CRC);
//! 3. if `STAGE2.BIN` loads: remove `STAGE2.OLD` and rename `.BIN` to `.OLD`;
//!    else (missing or broken, e.g. after an install cut between 3 and 4): remove `.BIN`
//!    and keep `.OLD`, the one that still boots;
//! 4. rename `STAGE2.NEW` to `STAGE2.BIN`.
//!
//! Until 3 renames it, `.BIN` is a bootable image (or, when it isn't, `.OLD` is and is
//! kept); from then to 4, `.OLD` is. So one cut install after another never leaves only
//! `.NEW`.

use super::{BlockDevice, DirEntry, FatError, Volume};
use crate::header::{ImageError, ImageHeader, HEADER_LEN};

/// The directory, from the root.
pub const DIR: &str = "VUX9K";
pub const BIN: [u8; 11] = *b"STAGE2  BIN";
pub const OLD: [u8; 11] = *b"STAGE2  OLD";
pub const NEW: [u8; 11] = *b"STAGE2  NEW";

/// Why a file can't be started (or installed).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BootError {
    Fat(FatError),
    /// Not a VUX9 v4 file (or shorter than its header).
    NotV4,
    /// The header's size and the file's disagree.
    SizeMismatch,
    /// The header's load address, mode or entry don't fit (`ImageHeader::check`).
    Image(ImageError),
    /// The payload's CRC isn't the header's.
    Crc,
}

impl From<FatError> for BootError {
    fn from(e: FatError) -> Self {
        BootError::Fat(e)
    }
}

/// The file `select_boot` chose.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Which {
    Bin,
    Old,
}

/// Where a payload may load: `base..base + len` (the PSRAM for the second stage).
#[derive(Clone, Copy, Debug)]
pub struct Window {
    pub base: u32,
    pub len: u32,
}

/// Check the v4 file `file` and hand its payload to `load` (offset into the payload,
/// bytes) as it is read: the header first (parse, size, `check(window)`), the CRC at the
/// end. On an error, `load` may have received part of the payload.
pub fn load_file<D: BlockDevice>(
    vol: &mut Volume<D>,
    file: &DirEntry,
    window: Window,
    load: &mut dyn FnMut(u32, &[u8]),
) -> Result<ImageHeader, BootError> {
    let mut header: Option<Result<ImageHeader, BootError>> = None;
    let mut crc = 0xFFFF_FFFF;
    let mut at = 0u32;
    vol.for_each_sector(file, &mut |data| {
        let mut payload = data;
        if header.is_none() {
            // The first sector holds the whole header (sectors are 512 bytes)
            header = Some(match ImageHeader::parse(data) {
                None => Err(BootError::NotV4),
                Some(h) if h.size as u64 + HEADER_LEN as u64 != file.size as u64 => {
                    Err(BootError::SizeMismatch)
                }
                Some(h) => h
                    .check(window.base, window.len)
                    .map(|()| h)
                    .map_err(BootError::Image),
            });
            payload = &data[HEADER_LEN.min(data.len())..];
        }
        if let Some(Ok(_)) = header {
            crc = crate::crc32_update(payload, crc);
            load(at, payload);
            at += payload.len() as u32;
        }
    })?;
    let h = header.unwrap_or(Err(BootError::NotV4))?;
    if crc ^ 0xFFFF_FFFF != h.crc32 {
        return Err(BootError::Crc);
    }
    Ok(h)
}

/// Load the second stage: `.BIN`, or `.OLD` when `.BIN` is missing or broken. Errors:
/// `.BIN`'s and `.OLD`'s.
pub fn select_boot<D: BlockDevice>(
    vol: &mut Volume<D>,
    window: Window,
    load: &mut dyn FnMut(u32, &[u8]),
) -> Result<(Which, ImageHeader), (BootError, BootError)> {
    let mut try_one = |name: &[u8; 11]| -> Result<ImageHeader, BootError> {
        let dir = vol.open(DIR)?;
        let file = vol.find(&dir, name)?.ok_or(FatError::NotFound)?;
        load_file(vol, &file, window, load)
    };
    let bin = match try_one(&BIN) {
        Ok(h) => return Ok((Which::Bin, h)),
        Err(e) => e,
    };
    match try_one(&OLD) {
        Ok(h) => Ok((Which::Old, h)),
        Err(old) => Err((bin, old)),
    }
}

/// Install `image` (a whole v4 file) as the second stage, keeping the current one as
/// `.OLD` (module comment). The image is checked against `window` before anything is
/// written.
#[cfg(feature = "fat-write")]
pub fn install<D: BlockDevice>(
    vol: &mut Volume<D>,
    window: Window,
    image: &[u8],
) -> Result<(), BootError> {
    let h = ImageHeader::parse(image).ok_or(BootError::NotV4)?;
    if h.size as u64 + HEADER_LEN as u64 != image.len() as u64 {
        return Err(BootError::SizeMismatch);
    }
    h.check(window.base, window.len).map_err(BootError::Image)?;
    if !h.crc_matches(&image[HEADER_LEN..]) {
        return Err(BootError::Crc);
    }
    let dir = vol.open(DIR)?;
    if let Some(stale) = vol.find(&dir, &NEW)? {
        vol.remove(&stale)?;
    }
    let new = vol.create(&dir, &NEW, image)?;
    let mut back = 0xFFFF_FFFF;
    let mut len = 0usize;
    vol.for_each_sector(&new, &mut |d| {
        back = crate::crc32_update(d, back);
        len += d.len();
    })?;
    if len != image.len() || back != crate::crc32_update(image, 0xFFFF_FFFF) {
        return Err(FatError::Verify.into());
    }
    if let Some(bin) = vol.find(&dir, &BIN)? {
        if load_file(vol, &bin, window, &mut |_, _| ()).is_ok() {
            if let Some(old) = vol.find(&dir, &OLD)? {
                vol.remove(&old)?;
            }
            vol.rename(&dir, &bin, &OLD)?;
        } else {
            vol.remove(&bin)?;
        }
    }
    vol.rename(&dir, &new, &BIN)?;
    Ok(())
}
