// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! fw_common::fat on a card image file, from the host: what sim/emu/test_fat_images.py
//! runs so that fsck.fat and mtools can check what fw_common wrote.
//!
//! Paths go through `Volume::resolve` (8.3 or long names, `.` and `..`), from the root.
//!
//! ```text
//! fat_host IMAGE ls DIR            entries of DIR, one per line: 8.3 name[, tab, long name]
//! fat_host IMAGE cat FILE          FILE's bytes to stdout
//! fat_host IMAGE rm FILE           remove FILE (and its long name)
//! fat_host IMAGE put FILE SRC      create FILE (8.3) with the bytes of the host file SRC
//! fat_host IMAGE mkdir DIR         make DIR (8.3)
//! fat_host IMAGE rmdir DIR         remove DIR, if empty
//! fat_host IMAGE mv FILE DEST      move FILE to DEST (8.3; in the same or another directory)
//! fat_host IMAGE free              the free clusters, counted in the FAT
//! fat_host IMAGE install V4FILE    install V4FILE as \VUX9K\STAGE2.BIN (.OLD keeps the old)
//! fat_host IMAGE boot              which second stage would start, and its version
//! ```

use std::io::Write;
use std::process::ExitCode;

use fw_common::fat::stage2::{install, select_boot, Which, Window};
use fw_common::fat::{BlockDevice, Volume, SECTOR};
use fw_common::map::{XMEM_BASE, XMEM_BYTES};

struct FileDisk(Vec<u8>);

impl BlockDevice for FileDisk {
    fn read(&mut self, lba: u32, buf: &mut [u8; SECTOR]) -> bool {
        let at = lba as usize * SECTOR;
        match self.0.get(at..at + SECTOR) {
            Some(s) => {
                buf.copy_from_slice(s);
                true
            }
            None => false,
        }
    }
    fn write(&mut self, lba: u32, buf: &[u8; SECTOR]) -> bool {
        let at = lba as usize * SECTOR;
        match self.0.get_mut(at..at + SECTOR) {
            Some(s) => {
                s.copy_from_slice(buf);
                true
            }
            None => false,
        }
    }
}

const PSRAM: Window = Window {
    base: XMEM_BASE as u32,
    len: XMEM_BYTES as u32,
};

fn run(args: &[String]) -> Result<(), String> {
    let [image, cmd, rest @ ..] = args else {
        return Err(
            "usage: fat_host IMAGE ls|cat|rm|put|mkdir|rmdir|mv|free|install|boot [ARG...]".into(),
        );
    };
    let data = std::fs::read(image).map_err(|e| format!("{image}: {e}"))?;
    let mut vol = Volume::mount(FileDisk(data)).map_err(|e| format!("mount: {e:?}"))?;
    let arg = rest.first().map(String::as_str).unwrap_or("");
    let arg2 = rest.get(1).map(String::as_str).unwrap_or("");
    let root = vol.root();
    let mut changed = false;
    match cmd.as_str() {
        "ls" => {
            let dir = vol
                .resolve(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            vol.list(&dir, &mut |e, long| {
                let mut name = [0u8; 12];
                let n = e.display_name(&mut name);
                let short = String::from_utf8_lossy(&name[..n]);
                match long {
                    Some(l) => println!("{short}\t{}", String::from_utf8_lossy(l)),
                    None => println!("{short}"),
                }
                false
            })
            .map_err(|e| format!("{arg}: {e:?}"))?;
        }
        "cat" => {
            let f = vol
                .resolve(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            let mut out = Vec::new();
            vol.for_each_sector(&f, &mut |d| out.extend_from_slice(d))
                .map_err(|e| format!("{arg}: {e:?}"))?;
            std::io::stdout()
                .write_all(&out)
                .map_err(|e| e.to_string())?;
        }
        "rm" => {
            let (_, f) = vol
                .locate(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            vol.remove(&f).map_err(|e| format!("{arg}: {e:?}"))?;
            changed = true;
        }
        "put" => {
            let src = std::fs::read(arg2).map_err(|e| format!("{arg2}: {e}"))?;
            let (dir, name) = vol
                .resolve_parent(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            vol.create(&dir, &name, &src)
                .map_err(|e| format!("{arg}: {e:?}"))?;
            changed = true;
        }
        "mkdir" => {
            let (dir, name) = vol
                .resolve_parent(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            vol.mkdir(&dir, &name)
                .map_err(|e| format!("{arg}: {e:?}"))?;
            changed = true;
        }
        "rmdir" => {
            let (_, d) = vol
                .locate(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            vol.rmdir(&d).map_err(|e| format!("{arg}: {e:?}"))?;
            changed = true;
        }
        "mv" => {
            let (src_dir, f) = vol
                .locate(&root, arg.as_bytes())
                .map_err(|e| format!("{arg}: {e:?}"))?;
            let (dst_dir, name) = vol
                .resolve_parent(&root, arg2.as_bytes())
                .map_err(|e| format!("{arg2}: {e:?}"))?;
            vol.move_to(&src_dir, &f, &dst_dir, &name)
                .map_err(|e| format!("{arg} -> {arg2}: {e:?}"))?;
            changed = true;
        }
        "free" => {
            let n = vol.free_clusters().map_err(|e| format!("free: {e:?}"))?;
            println!("{n}");
        }
        "install" => {
            let file = std::fs::read(arg).map_err(|e| format!("{arg}: {e}"))?;
            install(&mut vol, PSRAM, &file).map_err(|e| format!("install: {e:?}"))?;
            changed = true;
        }
        "boot" => {
            let (which, h) =
                select_boot(&mut vol, PSRAM, &mut |_, _| ()).map_err(|e| format!("boot: {e:?}"))?;
            let which = if which == Which::Bin { "BIN" } else { "OLD" };
            println!("{which} {}", h.version);
        }
        _ => return Err(format!("unknown command {cmd}")),
    }
    if changed {
        std::fs::write(image, &vol.device().0).map_err(|e| format!("{image}: {e}"))?;
    }
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("fat_host: {e}");
            ExitCode::FAILURE
        }
    }
}
