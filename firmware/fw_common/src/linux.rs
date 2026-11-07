// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage's `linux`, the part without MMIO: an upstream riscv kernel `Image`
//! (linux arch/riscv/kernel/head.S's header, Documentation/arch/riscv/boot-image-header.rst)
//! and a flattened device tree as dtc writes it, both unmodified files on the card, checked
//! (`check_image`, `check_fdt`) and loaded where the kernel wants them (`load`): the device
//! tree at `map::LINUX_DTB_ADDR`, the Image at `map::LINUX_IMAGE_BASE`, as the emulator's
//! `Soc::load_linux` puts them. The handoff (a0 = 0, a1 = the device tree) is the stage's.

use crate::fat::{BlockDevice, DirEntry, FatError, Volume};
use crate::fmt::Sink;
use crate::map;

/// The Image header's length: code0/code1, text_offset, image_size, flags, version,
/// reserved, magic ("RISCV"), magic2, reserved.
pub const IMAGE_HEADER_LEN: usize = 64;
/// magic2 at +0x38: what identifies the header (`RISCV_IMAGE_MAGIC2`, asm/image.h).
pub const IMAGE_MAGIC2: [u8; 4] = *b"RSC\x05";
/// A device tree's header: ten big-endian words, magic first, totalsize second.
pub const FDT_HEADER_LEN: usize = 40;
pub const FDT_MAGIC: u32 = 0xD00D_FEED;

/// Why `linux` doesn't start a kernel (besides the card's errors).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LinuxError {
    /// Shorter than its header, no magic2, or an image_size of 0.
    NotImage,
    /// A text_offset other than 0: the kernel runs where it loads, at the PSRAM's start.
    TextOffset,
    /// The file runs into the second stage (`map::LINUX_IMAGE_MAX`).
    ImageTooBig,
    /// image_size (the file and its bss) runs into the device tree.
    Overlap,
    /// Shorter than its header, or no magic.
    NotFdt,
    /// A totalsize other than the file's size.
    FdtSize,
    /// More than `map::LINUX_DTB_MAX`.
    FdtTooBig,
}

fn le64(b: &[u8], at: usize) -> u64 {
    let mut w = [0u8; 8];
    w.copy_from_slice(&b[at..at + 8]);
    u64::from_le_bytes(w)
}

fn be32(b: &[u8], at: usize) -> u32 {
    u32::from_be_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// An Image's header (`head`) and the file's size: one the stage starts at the PSRAM's
/// start, whose file ends below the second stage and whose image_size below the device
/// tree. The u64 fields are compared whole (a high word that isn't 0 is out of reach).
pub fn check_image(head: &[u8; IMAGE_HEADER_LEN], file_size: u32) -> Result<(), LinuxError> {
    let image_size = le64(head, 16);
    if head[0x38..0x3C] != IMAGE_MAGIC2 || image_size == 0 {
        return Err(LinuxError::NotImage);
    }
    if le64(head, 8) != 0 {
        return Err(LinuxError::TextOffset);
    }
    if file_size as usize > map::LINUX_IMAGE_MAX {
        return Err(LinuxError::ImageTooBig);
    }
    if image_size > (map::LINUX_DTB_ADDR - map::LINUX_IMAGE_BASE) as u64 {
        return Err(LinuxError::Overlap);
    }
    Ok(())
}

/// A device tree's first 8 bytes (magic, totalsize) and the file's size: the whole file
/// is the tree, which fits its room below the second stage's stack.
pub fn check_fdt(head: &[u8; 8], file_size: u32) -> Result<(), LinuxError> {
    if be32(head, 0) != FDT_MAGIC || (file_size as usize) < FDT_HEADER_LEN {
        return Err(LinuxError::NotFdt);
    }
    if be32(head, 4) != file_size {
        return Err(LinuxError::FdtSize);
    }
    if file_size as usize > map::LINUX_DTB_MAX {
        return Err(LinuxError::FdtTooBig);
    }
    Ok(())
}

/// Check both files' headers, then read the device tree into `fdt` (its room at
/// `LINUX_DTB_ADDR`, `LINUX_DTB_MAX` bytes) and the Image into `kernel` (the PSRAM from
/// `LINUX_IMAGE_BASE`, `LINUX_IMAGE_MAX` bytes), each straight into place
/// (`Volume::read_file_to`). Nothing is written before both headers are checked; on a card
/// error after that, the two may hold part of the files. The error type is the caller's.
pub fn load<D: BlockDevice, E: From<FatError> + From<LinuxError>>(
    vol: &mut Volume<D>,
    image: &DirEntry,
    dtb: &DirEntry,
    kernel: &mut [u8],
    fdt: &mut [u8],
) -> Result<(), E> {
    if (image.size as usize) < IMAGE_HEADER_LEN {
        return Err(LinuxError::NotImage.into());
    }
    let mut head = [0u8; IMAGE_HEADER_LEN];
    vol.read_file_to(image, 0, &mut head)?;
    check_image(&head, image.size)?;
    if (dtb.size as usize) < FDT_HEADER_LEN {
        return Err(LinuxError::NotFdt.into());
    }
    let mut head = [0u8; 8];
    vol.read_file_to(dtb, 0, &mut head)?;
    check_fdt(&head, dtb.size)?;
    vol.read_file_to(dtb, 0, &mut fdt[..dtb.size as usize])?;
    vol.read_file_to(image, 0, &mut kernel[..image.size as usize])?;
    Ok(())
}

// The texts spell the addresses and sizes out
const _: () = assert!(
    map::LINUX_IMAGE_BASE == 0x8000_0000
        && map::LINUX_IMAGE_MAX == 7 * 1024 * 1024
        && map::LINUX_DTB_ADDR == 0x807F_0000
        && map::LINUX_DTB_MAX == 56 * 1024,
    "update put_linux_error's texts"
);

/// Print why `linux` refused its files, on one line without its end.
pub fn put_linux_error(out: &mut impl Sink, e: LinuxError) {
    out.put_str(match e {
        LinuxError::NotImage => "Image: not a RISC-V Linux Image",
        LinuxError::TextOffset => "Image: text_offset not 0 (it runs at 80000000)",
        LinuxError::ImageTooBig => "Image: over 7 MB (it must end below the second stage)",
        LinuxError::Overlap => "Image: image_size reaches the device tree at 807F0000",
        LinuxError::NotFdt => "DTB: not a flattened device tree",
        LinuxError::FdtSize => "DTB: totalsize is not the file's size",
        LinuxError::FdtTooBig => "DTB: over 56 KB",
    });
}
