// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Whether a slot-0 image may replace the running Boot Manager, before its CRC is
//! checked (the checks of check_boot_manager_update, in order).

use crate::SlotHeader;

/// Lower I-RAM below the Resident Loader.
pub const MAX_IMAGE: u32 = 14 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Precheck {
    /// Candidate: read the whole image and check its CRC.
    Candidate,
    /// Not an RV32 image: ignored silently.
    NotRiscv,
    /// Size 0 or over MAX_IMAGE: reported.
    BadSize(u32),
    /// Not newer than the running version: ignored silently.
    NotNewer,
    /// First instruction all zeros or all ones (an erased image): reported.
    BadEntry(u32),
}

/// `entry_instr` is the image's first word (sector bytes 64-67).
pub fn precheck(h: &SlotHeader, running_version: u32, entry_instr: u32) -> Precheck {
    if !h.is_riscv() {
        Precheck::NotRiscv
    } else if h.size == 0 || h.size > MAX_IMAGE {
        Precheck::BadSize(h.size)
    } else if h.version <= running_version {
        Precheck::NotNewer
    } else if entry_instr == 0 || entry_instr == 0xFFFF_FFFF {
        Precheck::BadEntry(entry_instr)
    } else {
        Precheck::Candidate
    }
}
