// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The word the Boot Manager leaves at 0x2000_1FFC for the Resident Loader
//! (firmware/resident_loader decodes it).

/// "Launch slot N": bits 7:0 the slot, bit 8 the card is SDHC.
pub fn launch(slot: u32, sdhc: bool) -> u32 {
    slot | sdhc_bit(sdhc)
}

/// "Install slot 0 into lower I-RAM" (self-update), with the SDHC bit.
pub const SLOT_UPDATE_MAGIC: u32 = 0xA55A_0000;
pub fn update(sdhc: bool) -> u32 {
    SLOT_UPDATE_MAGIC | sdhc_bit(sdhc)
}

/// Left by the Resident Loader for the Boot Manager it has just installed.
pub const SLOT_UPDATED_BOOT_MAGIC: u32 = 0x5A5A_B002;

fn sdhc_bit(sdhc: bool) -> u32 {
    if sdhc {
        0x100
    } else {
        0
    }
}
