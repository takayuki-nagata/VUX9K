// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage's launcher, the part without MMIO: where a v4 file's payload runs
//! (`plan`), where it is staged while it loads (`staging`), and the text of what keeps
//! it from starting (`put_run_error`).
//!
//! | Target | Header | Start |
//! |---|---|---|
//! | `Psram` | `MODE_RISCV`, inside `XMEM_BASE..STAGE2_BASE` | loaded in place; D-RAM zeroed, `fence.i`, a jump to the entry with a0-a2 as the first stage hands them over |
//! | `IramRv` | `MODE_RISCV`, `load_addr` and entry 0, inside the 14 KB of `IRAM_APP_BYTES` | copied into I-RAM, D-RAM zeroed, `BOOT_MODE_RV32`, soft reset |
//! | `IramHack` | `MODE_HACK`, a word-aligned `load_addr` inside I-RAM | as `IramRv`, with the Hack boot mode |
//!
//! An I-RAM payload replaces the first stage, so it is written there only after its CRC
//! checked: the file loads through `staging` (I-RAM addresses into the PSRAM at
//! `SCRATCH`, PSRAM addresses in place) with `LOAD_WINDOW`, which holds every target's
//! window, and `plan` then decides from the checked header.

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
/// picks the target): from I-RAM's start to the second stage.
pub const LOAD_WINDOW: Window = Window {
    base: map::IRAM_BASE as u32,
    len: (map::STAGE2_BASE - map::IRAM_BASE) as u32,
};

/// Where an application runs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    /// RV32 code in the PSRAM below the second stage, started by a jump.
    Psram,
    /// RV32 code at I-RAM's start, started by a soft reset.
    IramRv,
    /// A Hack program in I-RAM, started by a soft reset.
    IramHack,
}

impl Target {
    /// Where the target's payload may load.
    pub fn window(self) -> Window {
        match self {
            Target::Psram => Window {
                base: map::XMEM_BASE as u32,
                len: (map::STAGE2_BASE - map::XMEM_BASE) as u32,
            },
            Target::IramRv => Window {
                base: map::IRAM_BASE as u32,
                len: map::IRAM_APP_BYTES as u32,
            },
            Target::IramHack => Window {
                base: map::IRAM_BASE as u32,
                len: map::IRAM_BYTES as u32,
            },
        }
    }

    /// `GPIO_BOOT_MODE` for the soft reset into an I-RAM target (None: a jump).
    pub fn boot_mode(self) -> Option<u32> {
        match self {
            Target::Psram => None,
            Target::IramRv => Some(map::BOOT_MODE_RV32),
            Target::IramHack => Some(map::BOOT_MODE_VALID),
        }
    }
}

/// The target a checked header runs on (the module comment's table): RV32 loading at 0
/// runs from I-RAM, elsewhere from the PSRAM; Hack from I-RAM. `OutOfWindow` when the
/// payload leaves the target's window, or a Hack payload doesn't start on an I-RAM word;
/// `BadEntry` for RV32 in I-RAM with an entry other than 0 (the soft reset starts there).
pub fn plan(h: &ImageHeader) -> Result<Target, ImageError> {
    let target = match h.mode {
        MODE_RISCV if h.load_addr == map::IRAM_BASE as u32 => Target::IramRv,
        MODE_RISCV => Target::Psram,
        MODE_HACK => Target::IramHack,
        m => return Err(ImageError::BadMode(m)),
    };
    let w = target.window();
    h.check(w.base, w.len)?;
    // The copy into I-RAM writes whole words (I-RAM has no byte enables)
    if target != Target::Psram && h.load_addr & 3 != 0 {
        return Err(ImageError::OutOfWindow);
    }
    // The soft reset starts RV32 at 0, whatever the header's entry says
    if target == Target::IramRv && h.entry_point != 0 {
        return Err(ImageError::BadEntry(h.entry_point));
    }
    Ok(target)
}

/// Where a payload chunk for address `addr` is stored while it loads (`load_file` within
/// `LOAD_WINDOW`): an I-RAM address at `SCRATCH` plus it, a PSRAM one in place, any
/// other (a header `plan` refuses) nowhere. A chunk is at most a sector, so one that
/// starts in I-RAM ends in the PSRAM's first 16.5 KB.
pub fn staging(addr: u32) -> Option<u32> {
    if addr < (map::IRAM_BASE + map::IRAM_BYTES) as u32 {
        Some(SCRATCH + addr)
    } else if addr >= map::XMEM_BASE as u32 {
        Some(addr)
    } else {
        None
    }
}

// OutOfWindow's message spells the windows out (no number formatting for a range)
const _: () = assert!(
    map::XMEM_BASE == 0x8000_0000
        && map::STAGE2_BASE == 0x8070_0000
        && map::IRAM_APP_BYTES == 0x3800
        && map::IRAM_BYTES == 0x4000,
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
        BootError::Image(ImageError::OutOfWindow) => out.put_str(
            "loads outside 80000000-806FFFFF and 0-37FF (RV32 at 0), 0-3FFF (Hack, at a word)",
        ),
        _ => put_boot_error(out, e),
    }
}
