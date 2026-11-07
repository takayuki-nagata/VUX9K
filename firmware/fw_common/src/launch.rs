// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage's launcher, the part without MMIO: where a v4 file's payload runs
//! (`plan`), where it is staged while it loads (`staging`), and the text of what keeps
//! it from starting (`put_run_error`).
//!
//! | Target | Header | Start |
//! |---|---|---|
//! | `Psram` | `MODE_RISCV`, inside `XMEM_BASE..STAGE2_BASE` | loaded in place, `fence.i`, a jump to the entry with a0-a2 as the first stage hands them over |
//! | `HackSeg` | `MODE_HACK`, a word-aligned `load_addr` inside `0..HACK_I_BYTES`: an offset in Hack's instruction segment | loaded in place (`HACK_I_BASE` + offset), Hack's data segment zeroed, `fence.i`, the Hack boot mode (`BOOT_MODE_VALID`), soft reset; it starts at 0 |
//!
//! RV32 runs from the PSRAM only: an RV32 file loading at 0 (the boot ROM) is refused as out of
//! the window. `load` checks the header within `LOAD_WINDOW`, which holds every target's
//! window, `plan` decides the target from it, and only then does the payload load, through
//! `staging` (addresses below `HACK_I_BYTES` into the PSRAM at `SCRATCH`, which is
//! `HACK_I_BASE`, so a Hack payload lands in its segment; PSRAM addresses in place), into
//! the target's window. Nothing outside the PSRAM is written before the CRC is checked.

use crate::boot::put_boot_error;
use crate::fat::stage2::{BootError, Window};
use crate::fat::{BlockDevice, DirEntry, FatError, Volume};
use crate::fmt::Sink;
use crate::header::{ImageError, ImageHeader, HEADER_LEN, MODE_HACK, MODE_RISCV};
use crate::map;

/// Where a run's target is staged: the PSRAM's start (the first stage's data, below the
/// second stage, is not used once the second stage runs).
pub const SCRATCH: u32 = map::XMEM_BASE as u32;
/// The longest file `put` and `install` receive into `SCRATCH`: the PSRAM below the
/// second stage.
pub const SCRATCH_BYTES: u32 = (map::STAGE2_BASE - map::XMEM_BASE) as u32;

/// Every target's window together (`load` checks a header against it before `plan` picks
/// the target): from 0 (Hack's offsets) to the second stage.
pub const LOAD_WINDOW: Window = Window {
    base: map::ROM_BASE as u32,
    len: (map::STAGE2_BASE - map::ROM_BASE) as u32,
};

/// Where an application runs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    /// RV32 code in the PSRAM below the second stage, started by a jump.
    Psram,
    /// A Hack program in Hack's instruction segment, started by a soft reset.
    HackSeg,
}

impl Target {
    /// Where the target's payload may load.
    pub fn window(self) -> Window {
        match self {
            Target::Psram => Window {
                base: map::XMEM_BASE as u32,
                len: (map::STAGE2_BASE - map::XMEM_BASE) as u32,
            },
            Target::HackSeg => Window {
                base: 0,
                len: map::HACK_I_BYTES as u32,
            },
        }
    }
}

/// The target a checked header runs on (the module comment's table): RV32 from the
/// PSRAM, Hack from its segment. `OutOfWindow` when the payload leaves the target's window
/// (RV32 at 0 among them), or a Hack payload doesn't start on a word.
pub fn plan(h: &ImageHeader) -> Result<Target, ImageError> {
    let target = match h.mode {
        MODE_RISCV => Target::Psram,
        MODE_HACK => Target::HackSeg,
        m => return Err(ImageError::BadMode(m)),
    };
    let w = target.window();
    h.check(w.base, w.len)?;
    // Two instructions a word, the first in the low half: an image starts on a word
    if target == Target::HackSeg && h.load_addr & 3 != 0 {
        return Err(ImageError::OutOfWindow);
    }
    Ok(target)
}

/// Where a payload for address `addr` is stored while it loads (`load`, after `plan`): an
/// address below `HACK_I_BYTES` (a Hack offset; an RV32 one `plan` refuses) at `SCRATCH`
/// plus it, a PSRAM one in place, any other (a header `plan` refuses) nowhere.
pub fn staging(addr: u32) -> Option<u32> {
    if addr < map::HACK_I_BYTES as u32 {
        Some(SCRATCH + addr)
    } else if addr >= map::XMEM_BASE as u32 {
        Some(addr)
    } else {
        None
    }
}

/// Load the v4 file `file` for `run` into `psram` (the PSRAM from `SCRATCH` on, up to
/// the second stage, `SCRATCH_BYTES`): its header first (shorter than a header:
/// `NotV4`), checked within `LOAD_WINDOW` and given its target by `plan`, then the payload
/// straight to where `staging` puts it (`Volume::read_file_to`: runs of clusters at the
/// card's pace), then its CRC over what landed there. Nothing changes before the header
/// is checked, nothing outside the target's window after it; on an error after the header
/// the window may hold part of the payload.
pub fn load<D: BlockDevice>(
    vol: &mut Volume<D>,
    file: &DirEntry,
    psram: &mut [u8],
) -> Result<(ImageHeader, Target), BootError> {
    let mut head = [0u8; HEADER_LEN];
    vol.read_file_to(file, 0, &mut head).map_err(|e| match e {
        FatError::TooBig => BootError::NotV4,
        e => BootError::Fat(e),
    })?;
    let h = ImageHeader::parse(&head).ok_or(BootError::NotV4)?;
    if h.size as u64 + HEADER_LEN as u64 != file.size as u64 {
        return Err(BootError::SizeMismatch);
    }
    h.check(LOAD_WINDOW.base, LOAD_WINDOW.len)
        .map_err(BootError::Image)?;
    let target = plan(&h).map_err(BootError::Image)?;
    // plan keeps the payload in its target's window, which staging maps into the PSRAM
    let at = (staging(h.load_addr).unwrap_or(SCRATCH) - SCRATCH) as usize;
    let dst = &mut psram[at..at + h.size as usize];
    vol.read_file_to(file, HEADER_LEN as u32, dst)?;
    if !h.crc_matches(dst) {
        return Err(BootError::Crc);
    }
    Ok((h, target))
}

// A Hack payload is staged where it runs
const _: () = assert!(
    SCRATCH as usize == map::HACK_I_BASE,
    "staging must put a Hack offset into Hack's instruction segment"
);

// OutOfWindow's message spells the windows out (no number formatting for a range)
const _: () = assert!(
    map::XMEM_BASE == 0x8000_0000
        && map::STAGE2_BASE == 0x8070_0000
        && map::HACK_I_BYTES == 0x1_0000,
    "update OutOfWindow's text in put_run_error"
);

/// Print why an application can't start, on one line without its end: the second
/// stage's windows and modes, and `put_boot_error`'s text for the rest.
pub fn put_run_error(out: &mut impl Sink, e: BootError) {
    match e {
        BootError::Image(ImageError::BadMode(m)) => {
            out.put_str("not RV32 or Hack, mode ");
            out.put_dec(m);
        }
        BootError::Image(ImageError::OutOfWindow) => {
            out.put_str("loads outside 80000000-806FFFFF (RV32), 0-FFFF (Hack, at a word)")
        }
        _ => put_boot_error(out, e),
    }
}
