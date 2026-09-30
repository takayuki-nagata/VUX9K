// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The Boot Manager's `w` command: a host writes whole sectors of a slot over the
//! UART (tools/vux_tool.py flash_slot). Each step waits for the Boot Manager's line:
//!
//! ```text
//! BM: [READY]            host: slot ID, one raw byte 0-9
//! BM: [READY-SLOT:n]     host: sector count, one raw byte 1-64
//! BM: [READY-COUNT:n]    then per sector:
//! BM: [READY-SEC:i]      host: 512 bytes
//! BM: [SD] Successfully wrote ...   (or an [SD-ERR] line at any point)
//! ```

use crate::fmt::Sink;
use crate::header::SECTOR;
use crate::slot_sector;

/// Each byte of the exchange must arrive within this.
pub const BYTE_TIMEOUT_MS: u32 = 5000;
pub const MAX_SECTORS: u8 = 64;

/// The host side: the UART.
pub trait Host: Sink {
    /// The next byte, or None once `timeout_ms` passes without one.
    fn recv(&mut self, timeout_ms: u32) -> Option<u8>;
    /// Drop what arrived before the exchange started.
    fn drain(&mut self);
}

/// The card.
pub trait Disk {
    fn init(&mut self) -> bool;
    /// Write and verify one sector.
    fn write(&mut self, sector: u32, data: &[u8; SECTOR]) -> bool;
}

/// One header byte (slot ID or sector count) in `lo..=hi`, skipping CR/LF. Reports
/// what went wrong and returns None.
fn recv_param(host: &mut impl Host, lo: u8, hi: u8, what: &str) -> Option<u32> {
    loop {
        match host.recv(BYTE_TIMEOUT_MS) {
            Some(b'\r') | Some(b'\n') => continue,
            Some(b) if b >= lo && b <= hi => return Some(b as u32),
            Some(invalid) => {
                host.put_str("[SD-ERR] Invalid ");
                host.put_str(what);
                host.put_str(": 0x");
                host.put_hex_byte(invalid);
                host.put_str("\n");
            }
            None => {
                host.put_str("[SD-ERR] ");
                host.put_str(if lo == 0 { "Slot ID" } else { "Sector count" });
                host.put_str(" timeout!\n");
            }
        }
        return None;
    }
}

fn ready(host: &mut impl Host, what: &str, n: u32) {
    host.put_str("[READY-");
    host.put_str(what);
    host.put_dec(n);
    host.put_str("]\n");
}

/// Run one `w` exchange.
pub fn upload(host: &mut impl Host, disk: &mut impl Disk) {
    host.drain();
    host.put_str("[READY]\n");

    let Some(slot) = recv_param(host, 0, 9, "slot ID") else {
        return;
    };
    ready(host, "SLOT:", slot);
    let Some(count) = recv_param(host, 1, MAX_SECTORS, "sector count") else {
        return;
    };
    ready(host, "COUNT:", count);

    let mut buf = [0u8; SECTOR];
    if !disk.init() {
        host.put_str("[SD-ERR] Failed to initialize SD card!\n");
        return;
    }

    let base = slot_sector(slot);
    for i in 0..count {
        ready(host, "SEC:", i);
        for (n, b) in buf.iter_mut().enumerate() {
            match host.recv(BYTE_TIMEOUT_MS) {
                Some(v) => *b = v,
                None => {
                    host.put_str("[SD-ERR] Timeout at sector ");
                    host.put_dec(i);
                    host.put_str(", byte ");
                    host.put_dec(n as u32);
                    host.put_str("\n");
                    return;
                }
            }
        }
        if !disk.write(base + i, &buf) {
            host.put_str("[SD-ERR] Failed to write block at sector ");
            host.put_dec(base + i);
            host.put_str("\n");
            return;
        }
    }

    host.put_str("[SD] Successfully wrote ");
    host.put_dec(count);
    host.put_str(" sectors to Slot ");
    host.put_dec(slot);
    host.put_str(" (Sector ");
    host.put_dec(base);
    host.put_str(")! [OK]\n\n");
}
