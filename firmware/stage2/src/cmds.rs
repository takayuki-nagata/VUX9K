// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The file commands: paths resolved from the working directory (fw_common::fat's
//! `resolve`, `resolve_parent`, `locate`: `.`, `..`, 8.3 or long names), new names 8.3.
//! The card is mounted again for every command (the first stage's card, or one inserted
//! after a RAM boot without one). A card swapped for another while the stage runs is not
//! initialized again (fw_drivers' `ensure_init`), so the commands fail on it until `reset`;
//! the working directory is kept as a cluster of the card it was set on. Errors are one
//! `[ERR] ` line.

use fw_common::boot::put_boot_error;
use fw_common::fat::stage2::BootError;
use fw_common::fat::{DirEntry, FatError, Volume};
use fw_common::launch::{put_run_error, SCRATCH, SCRATCH_BYTES};
use fw_common::upload::{upload_v2, UploadSink, BLOCK};
use fw_drivers::sdcard::SdCard;
use fw_drivers::uart::Uart;

use crate::shell::{Ctx, PutBytes};

/// Why a command failed.
pub enum Error {
    Fat(FatError),
    Boot(BootError),
    /// No card answered.
    NoCard,
    /// A directory where a file is needed.
    IsDir,
    /// `rmdir` of the working directory.
    IsCwd,
    /// `cd` past `shell::PATH_MAX`.
    PathTooLong,
}

impl From<FatError> for Error {
    fn from(e: FatError) -> Self {
        Error::Fat(e)
    }
}

impl From<BootError> for Error {
    fn from(e: BootError) -> Self {
        Error::Boot(e)
    }
}

pub type Result = core::result::Result<(), Error>;

pub fn print_error(e: Error) {
    Uart::print_str("[ERR] ");
    match e {
        // A path component that isn't a directory, or a directory command on a file
        // (file commands check for directories themselves: IsDir)
        Error::Fat(FatError::WrongType) => Uart::print_str("not a directory"),
        Error::Fat(f) => put_boot_error(&mut Uart, BootError::Fat(f)),
        Error::Boot(b) => put_run_error(&mut Uart, b),
        Error::NoCard => Uart::print_str("no SD card"),
        Error::IsDir => Uart::print_str("is a directory"),
        Error::IsCwd => Uart::print_str("is the current directory"),
        Error::PathTooLong => Uart::print_str("path too long"),
    }
    Uart::print_str("\n");
}

/// The card's FAT32 volume and the working directory in it.
pub struct Card {
    pub vol: Volume<SdCard>,
    pub cwd: DirEntry,
}

/// The card: the one the first stage handed over, or else one initialized here
/// (inserted after a RAM boot without one).
pub fn mount(ctx: &Ctx) -> core::result::Result<Card, Error> {
    if !SdCard::ensure_init() {
        return Err(Error::NoCard);
    }
    let vol = Volume::mount(SdCard)?;
    let cwd = ctx.cwd.unwrap_or_else(|| vol.root());
    Ok(Card { vol, cwd })
}

/// `n` in decimal, right-aligned in `width` columns.
fn put_dec_right(n: u32, width: usize) {
    // Count the digits against powers of ten (times ten by shifts: no multiplier)
    let mut digits = 1;
    let mut p = 10u32;
    while digits < 10 && n >= p {
        digits += 1;
        p = p.wrapping_shl(3).wrapping_add(p << 1);
    }
    for _ in digits..width {
        Uart::write_byte(b' ');
    }
    Uart::print_dec(n);
}

/// One `ls` line: the 8.3 name, the size or `<DIR>`, the long name.
fn print_entry(e: &DirEntry, long: Option<&[u8]>) {
    let mut name = [0u8; 12];
    let n = e.display_name(&mut name);
    Uart.put_bytes(&name[..n]);
    for _ in n..13 {
        Uart::write_byte(b' ');
    }
    if e.is_dir() {
        Uart::print_str("     <DIR>");
    } else {
        put_dec_right(e.size, 10);
    }
    if let Some(l) = long {
        Uart::print_str("  ");
        Uart.put_bytes(l);
    }
    Uart::print_str("\n");
}

pub fn ls(c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let target = vol.resolve(cwd, path)?;
    if !target.is_dir() {
        print_entry(&target, None);
        return Ok(());
    }
    vol.list(&target, &mut |e, long| {
        // `.` and `..` (no other 8.3 name starts with a dot)
        if e.name[0] != b'.' {
            print_entry(e, long);
        }
        false
    })?;
    Ok(())
}

pub fn cd(ctx: &mut Ctx, c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let d = vol.resolve(cwd, path)?;
    if !d.is_dir() {
        return Err(FatError::WrongType.into());
    }
    ctx.path = ctx.path.join(path).ok_or(Error::PathTooLong)?;
    ctx.cwd = Some(d);
    Ok(())
}

/// `cat` (the bytes as they are, then a line end if they have none, also before an
/// error) or `hexdump` (16 bytes a line: offset, hex, ASCII).
pub fn show(c: &mut Card, path: &[u8], hex: bool) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let f = vol.resolve(cwd, path)?;
    if f.is_dir() {
        return Err(Error::IsDir);
    }
    let mut at = 0u32;
    let mut last = b'\n';
    let r = vol.for_each_sector(&f, &mut |data| {
        if !hex {
            Uart.put_bytes(data);
            last = data[data.len() - 1];
            return;
        }
        // Sectors are 512 bytes: lines don't cross them
        for line in data.chunks(16) {
            Uart::print_hex(at);
            Uart::print_str(" ");
            for i in 0..16 {
                match line.get(i) {
                    Some(&b) => {
                        Uart::print_str(" ");
                        Uart::print_hex_byte(b);
                    }
                    None => Uart::print_str("   "),
                }
            }
            Uart::print_str("  |");
            for &b in line {
                Uart::write_byte(if (0x20..=0x7E).contains(&b) { b } else { b'.' });
            }
            Uart::print_str("|\n");
            at += line.len() as u32;
        }
    });
    // Also before an error after part of the file
    if last != b'\n' {
        Uart::print_str("\n");
    }
    Ok(r?)
}

pub fn rm(c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let (_, e) = vol.locate(cwd, path)?;
    if e.is_dir() {
        return Err(Error::IsDir);
    }
    Ok(vol.remove(&e)?)
}

pub fn mkdir(c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let (dir, name) = vol.resolve_parent(cwd, path)?;
    vol.mkdir(&dir, &name)?;
    Ok(())
}

pub fn rmdir(c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let (_, e) = vol.locate(cwd, path)?;
    if !e.is_dir() {
        return Err(FatError::WrongType.into());
    }
    if e.cluster == cwd.cluster {
        return Err(Error::IsCwd);
    }
    Ok(vol.rmdir(&e)?)
}

/// `mv SRC DST`: into DST when it is a directory (keeping the 8.3 name), else to the
/// path DST.
pub fn mv(c: &mut Card, src: &[u8], dst: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let (src_dir, e) = vol.locate(cwd, src)?;
    if e.is_dir() {
        return Err(Error::IsDir);
    }
    let (dst_dir, name) = match vol.resolve(cwd, dst) {
        Ok(d) if d.is_dir() => (d, e.name),
        _ => vol.resolve_parent(cwd, dst)?,
    };
    vol.move_to(&src_dir, &e, &dst_dir, &name)?;
    Ok(())
}

/// Transfer v2 into the PSRAM at SCRATCH.
struct Scratch;

impl UploadSink for Scratch {
    fn begin(&mut self, _len: u32) -> bool {
        true
    }

    fn block(&mut self, index: u32, data: &[u8]) -> bool {
        let at = SCRATCH as usize + index as usize * BLOCK;
        unsafe { core::ptr::copy_nonoverlapping(data.as_ptr(), at as *mut u8, data.len()) }
        true
    }

    fn finish(&mut self, _ok: bool) -> bool {
        true
    }
}

/// A file over transfer v2 (at most `max` bytes), in SCRATCH; None after upload_v2
/// reported why not.
pub fn receive(max: u32) -> Option<&'static [u8]> {
    let len = upload_v2(&mut Uart, &mut Scratch, max)?;
    Some(unsafe { core::slice::from_raw_parts(SCRATCH as *const u8, len as usize) })
}

/// `put NAME`: the file over transfer v2, then written as NAME (8.3), replacing a file
/// of that name (`Volume::replace`: the old file stays until the new one is written).
/// The name is checked before the transfer.
pub fn put(c: &mut Card, path: &[u8]) -> Result {
    let (vol, cwd) = (&mut c.vol, &c.cwd);
    let (dir, name) = vol.resolve_parent(cwd, path)?;
    if vol.find(&dir, &name)?.is_some_and(|e| e.is_dir()) {
        return Err(Error::IsDir);
    }
    let Some(data) = receive(SCRATCH_BYTES) else {
        return Ok(());
    };
    vol.replace(&dir, &name, data)?;
    Uart::print_str("[PUT] OK ");
    Uart::print_dec(data.len() as u32);
    Uart::print_str(" bytes\n");
    Ok(())
}

pub fn info(ctx: &Ctx, c: &mut Card) -> Result {
    let vol = &mut c.vol;
    Uart::print_str(if SdCard::is_sdhc() {
        "[SD] SDHC"
    } else {
        "[SD] SDSC"
    });
    Uart::print_str(" card\n[FAT] FAT32 at LBA ");
    Uart::print_dec(vol.partition_lba());
    Uart::print_str(", ");
    let cluster = vol.cluster_bytes();
    Uart::print_dec(cluster);
    Uart::print_str(" B clusters, ");
    let free = vol.free_clusters()?;
    Uart::print_dec(free);
    Uart::print_str(" free (");
    // Clusters are 512 B to 64 KB, a power of two
    let shift = cluster.trailing_zeros();
    Uart::print_dec(if shift >= 10 {
        free << (shift - 10)
    } else {
        free >> (10 - shift)
    });
    Uart::print_str(" KB)\n[S1] v");
    Uart::print_dec(ctx.s1_version);
    Uart::print_str(" [S2] v");
    Uart::print_dec(crate::STAGE2_VERSION);
    Uart::print_str("\n");
    Ok(())
}
