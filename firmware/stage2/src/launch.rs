// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Starting things: an application (`run`, to fw_common::launch's targets), the first
//! stage again (`soft_reset`), and installing a second stage (`install`).

use fw_common::boot::{put_boot_error, RAM_BOOT_MAX, STAGE2_WINDOW};
use fw_common::fat::stage2::{self, load_file, BootError};
use fw_common::fat::Volume;
use fw_common::header::ImageHeader;
use fw_common::launch::{plan, staging, Target, LOAD_WINDOW, SCRATCH};
use fw_common::map;
use fw_drivers::sdcard::SdCard;
use fw_drivers::uart::Uart;

use crate::cmds::{self, Card, Error, Result};
use crate::shell::{Ctx, PutBytes};

fn write_word(addr: usize, w: u32) {
    unsafe { core::ptr::write_volatile(addr as *mut u32, w) }
}

/// D-RAM all zero, as at power-on: an application finds it so.
fn zero_dram() {
    for at in (map::DRAM_BASE..map::DRAM_BASE + map::DRAM_BYTES).step_by(4) {
        write_word(at, 0);
    }
}

/// `run FILE`: load and check it (a Hack program straight into its segment in the PSRAM:
/// nothing outside the PSRAM changes until the file has proved good), then start it.
/// Returns on an error.
pub fn run(ctx: &Ctx, c: &mut Card, path: &[u8]) -> Result {
    let vol = &mut c.vol;
    let file = vol.resolve(&c.cwd, path)?;
    if file.is_dir() {
        return Err(Error::IsDir);
    }
    let h = load_file(vol, &file, LOAD_WINDOW, &mut |addr, data| {
        if let Some(at) = staging(addr) {
            unsafe { core::ptr::copy_nonoverlapping(data.as_ptr(), at as *mut u8, data.len()) }
        }
    })?;
    let target = plan(&h).map_err(BootError::Image)?;
    Uart::print_str("[RUN] v");
    Uart::print_dec(h.version);
    Uart::print_str(" \"");
    Uart.put_bytes(&h.name[..h.name_len()]);
    Uart::print_str("\" ");
    Uart::print_dec(h.size);
    Uart::print_str(match target {
        Target::Psram => " B, PSRAM, starting at 0x",
        Target::HackSeg => " B, Hack segment, starting at 0x",
    });
    Uart::print_hex(match target {
        Target::Psram => h.entry_point,
        _ => 0,
    });
    Uart::print_str("\n\n");
    match target {
        Target::Psram => start_psram(&h, vol.partition_lba(), ctx.s1_version),
        Target::HackSeg => start_hack(&h),
    }
}

/// Jump to an application in the PSRAM with a0-a2 as the first stage hands them to the
/// second stage.
fn start_psram(h: &ImageHeader, part_lba: u32, s1_version: u32) -> ! {
    zero_dram();
    let sdhc = SdCard::is_sdhc() as u32;
    unsafe {
        core::arch::asm!(
            ".option push",
            ".option arch, +zifencei",
            "fence.i",
            ".option pop",
            "jr {entry}",
            entry = in(reg) h.entry_point,
            in("a0") sdhc,
            in("a1") part_lba,
            in("a2") s1_version,
            options(noreturn),
        );
    }
}

/// The checked payload is in Hack's instruction segment already (staged at SCRATCH plus
/// its offset, which is HACK_I_BASE plus it); the last word's bytes past it become 0. Then
/// Hack's data segment all zero (as D-RAM was), fence.i, and a soft reset into it in Hack
/// mode: it starts at 0.
fn start_hack(h: &ImageHeader) -> ! {
    let end = SCRATCH as usize + h.load_addr as usize + h.size as usize;
    let rest = end & 3;
    if rest != 0 {
        let at = end & !3;
        let w = unsafe { core::ptr::read_volatile(at as *const u32) };
        write_word(at, w & ((1u32 << (rest << 3)) - 1));
    }
    for i in 0..map::HACK_D_WORDS {
        write_word(map::HACK_D_BASE + (i << 2), 0);
    }
    unsafe {
        core::arch::asm!(
            ".option push",
            ".option arch, +zifencei",
            "fence.i",
            ".option pop"
        );
    }
    write_word(map::GPIO_BOOT_MODE, map::BOOT_MODE_VALID);
    reset_now()
}

/// What is left of the command's line, so that what starts next doesn't take it for
/// input (the first stage: for a key), then the soft reset.
fn reset_now() -> ! {
    while Uart::read_byte_timeout(5).is_some() {}
    write_word(map::GPIO_SOFT_RESET, map::SOFT_RESET_KEY);
    loop {} // cov:exclude(the soft reset takes the CPU before this)
}

/// `reset`: back to the first stage.
pub fn soft_reset() -> ! {
    Uart::print_str("[RESET] Soft reset\n");
    reset_now()
}

fn report(what: &str, r: core::result::Result<(), BootError>) {
    Uart::print_str(what);
    match r {
        Ok(()) => Uart::print_str("OK\n"),
        Err(e) => {
            put_boot_error(&mut Uart, e);
            Uart::print_str("\n");
        }
    }
}

/// `install`: a second stage over transfer v2, installed as `\VUX9K\STAGE2.BIN` (made if
/// the card has no `\VUX9K\`); `install -u` (tests) writes it there unchecked, keeping
/// `STAGE2.OLD`, to see the first stage fall back to it.
pub fn install(unchecked: bool) {
    let Some(file) = cmds::receive(RAM_BOOT_MAX) else {
        return;
    };
    // Without a card, the mount reports the read error
    SdCard::ensure_init();
    let vol = Volume::mount(SdCard).map_err(BootError::from);
    if !unchecked {
        let r = vol.and_then(|mut vol| stage2::install(&mut vol, STAGE2_WINDOW, file));
        report("[INSTALL] ", r);
        return;
    }
    let r = vol.and_then(|mut vol| {
        let dir = stage2::dir(&mut vol)?;
        if let Some(bin) = vol.find(&dir, &stage2::BIN)? {
            vol.remove(&bin)?;
        }
        vol.create(&dir, &stage2::BIN, file)?;
        Ok(())
    });
    report("[UNCHECKED] ", r);
}
