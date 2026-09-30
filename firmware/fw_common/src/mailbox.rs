// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The word the Boot Manager leaves at map::MAILBOX for the Resident Loader
//! (firmware/resident_loader decodes it). Bits 31:16 say what is asked, bit 8 the
//! card is SDHC, bits 7:0 the slot. The RL takes any other value (0 included) as
//! "nothing to load" and restarts the Boot Manager.

/// Bits 31:16 of a request.
pub const KIND_MASK: u32 = 0xFFFF_0000;
/// "Launch slot N".
pub const LAUNCH_MAGIC: u32 = 0xB007_0000;
/// "Install slot 0 into lower I-RAM" (self-update).
pub const SLOT_UPDATE_MAGIC: u32 = 0xA55A_0000;
/// Bit 8: the card is SDHC (block addressing).
pub const SDHC: u32 = 0x100;

pub fn launch(slot: u32, sdhc: bool) -> u32 {
    LAUNCH_MAGIC | sdhc_bit(sdhc) | slot
}

pub fn update(sdhc: bool) -> u32 {
    SLOT_UPDATE_MAGIC | sdhc_bit(sdhc)
}

/// Left by the Resident Loader for the Boot Manager it has just installed.
pub const SLOT_UPDATED_BOOT_MAGIC: u32 = 0x5A5A_B002;

fn sdhc_bit(sdhc: bool) -> u32 {
    if sdhc {
        SDHC
    } else {
        0
    }
}
