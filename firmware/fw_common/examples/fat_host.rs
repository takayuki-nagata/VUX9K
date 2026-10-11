// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! fw_common::fat on a card image file, from the host: what sim/emu/test_fat_images.py
//! runs so that fsck.fat and mtools can check what fw_common wrote.
//!
//! ```text
//! fat_host IMAGE ls DIR            names in DIR, one per line ("" is the root)
//! fat_host IMAGE cat FILE          FILE's bytes to stdout
//! fat_host IMAGE rm FILE           remove FILE (and its long name)
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
        return Err("usage: fat_host IMAGE ls|cat|rm|install|boot [ARG]".into());
    };
    let data = std::fs::read(image).map_err(|e| format!("{image}: {e}"))?;
    let mut vol = Volume::mount(FileDisk(data)).map_err(|e| format!("mount: {e:?}"))?;
    let arg = rest.first().map(String::as_str).unwrap_or("");
    let mut changed = false;
    match cmd.as_str() {
        "ls" => {
            let dir = vol.open(arg).map_err(|e| format!("{arg}: {e:?}"))?;
            vol.find_entry(&dir, &mut |e| {
                let mut name = [0u8; 12];
                let n = e.display_name(&mut name);
                println!("{}", String::from_utf8_lossy(&name[..n]));
                false
            })
            .map_err(|e| format!("{arg}: {e:?}"))?;
        }
        "cat" => {
            let f = vol.open(arg).map_err(|e| format!("{arg}: {e:?}"))?;
            let mut out = Vec::new();
            vol.for_each_sector(&f, &mut |d| out.extend_from_slice(d))
                .map_err(|e| format!("{arg}: {e:?}"))?;
            std::io::stdout()
                .write_all(&out)
                .map_err(|e| e.to_string())?;
        }
        "rm" => {
            let f = vol.open(arg).map_err(|e| format!("{arg}: {e:?}"))?;
            vol.remove(&f).map_err(|e| format!("{arg}: {e:?}"))?;
            changed = true;
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
