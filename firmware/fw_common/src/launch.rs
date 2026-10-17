// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage's launcher, the part without MMIO: where a v4 file's payload runs
//! (`plan`), where it is staged while it loads (`staging`), and the text of what keeps
//! it from starting (`put_run_error`).
//!
//! | Target | Header | Start |
//! |---|---|---|
//! | `Psram` | `MODE_RISCV`, inside `XMEM_BASE..STAGE2_BASE` | loaded in place; D-RAM zeroed, `fence.i`, a jump to the entry with a0-a2 as the first stage hands them over |
//! | `HackSeg` | `MODE_HACK`, a word-aligned `load_addr` inside `0..HACK_I_BYTES`: an offset in Hack's instruction segment | loaded in place (`HACK_I_BASE` + offset), Hack's data segment zeroed, `fence.i`, the Hack boot mode (`BOOT_MODE_VALID`), soft reset; it starts at 0 |
//!
//! RV32 runs from the PSRAM only: an RV32 file loading at 0 (I-RAM) is refused as out of
//! the window. The file loads through `staging` (addresses below `HACK_I_BYTES` into the
//! PSRAM at `SCRATCH`, which is `HACK_I_BASE`, so a Hack payload lands in its segment;
//! PSRAM addresses in place) with `LOAD_WINDOW`, which holds every target's window, and
//! `plan` then decides from the checked header. Nothing outside the PSRAM is written
//! before that: a refused file leaves the first stage in I-RAM.

use crate::boot::put_boot_error;
use crate::fat::stage2::{BootError, Window};
use crate::fmt::Sink;
use crate::header::{ImageError, ImageHeader, MODE_HACK, MODE_RISCV};
use crate::map;

/// Where a run's target is staged: the PSRAM's start (the first stage's data, below the
/// second stage, is not used once the second stage runs).
pub const SCRATCH: u32 = map::XMEM_BASE as u32;
/// The longest file `put` and `install` receive into `SCRATCH`: the PSRAM below the
/// second stage.
pub const SCRATCH_BYTES: u32 = (map::STAGE2_BASE - map::XMEM_BASE) as u32;

/// Every target's window together (`load_file` checks a header against it before `plan`
/// picks the target): from 0 (Hack's offsets) to the second stage.
pub const LOAD_WINDOW: Window = Window {
    base: map::IRAM_BASE as u32,
    len: (map::STAGE2_BASE - map::IRAM_BASE) as u32,
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

/// Where a payload chunk for address `addr` is stored while it loads (`load_file` within
/// `LOAD_WINDOW`): an address below `HACK_I_BYTES` (a Hack offset; an RV32 one `plan`
/// refuses) at `SCRATCH` plus it, a PSRAM one in place, any other (a header `plan`
/// refuses) nowhere. A chunk is at most a sector, so one that starts below `HACK_I_BYTES`
/// ends in the PSRAM's first 64.5 KB.
pub fn staging(addr: u32) -> Option<u32> {
    if addr < map::HACK_I_BYTES as u32 {
        Some(SCRATCH + addr)
    } else if addr >= map::XMEM_BASE as u32 {
        Some(addr)
    } else {
        None
    }
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
