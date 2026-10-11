// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The Boot Manager's `w` command (v1): a host writes whole sectors of a slot over the
//! UART (tools/vux_tool.py flash_slot). Each step waits for the Boot Manager's line:
//!
//! ```text
//! BM: [READY]            host: slot ID, one raw byte 0-9
//! BM: [READY-SLOT:n]     host: sector count, one raw byte 1-64
//! BM: [READY-COUNT:n]    then per sector:
//! BM: [READY-SEC:i]      host: 512 bytes
//! BM: [SD] Successfully wrote ...   (or an [SD-ERR] line at any point)
//! ```
//!
//! And transfer v2 (`upload_v2`), the two-stage boot's replacement for it.

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

/// One header byte (slot ID or sector count) in `lo..=hi`. Reports what went wrong
/// and returns None. `skip_line_ends`: ignore CR/LF, which a terminal may send after
/// the `w`; only before the slot ID, since a sector count can be 10 or 13.
fn recv_param(
    host: &mut impl Host,
    lo: u8,
    hi: u8,
    what: &str,
    skip_line_ends: bool,
) -> Option<u32> {
    loop {
        match host.recv(BYTE_TIMEOUT_MS) {
            Some(b'\r') | Some(b'\n') if skip_line_ends => continue,
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

    let Some(slot) = recv_param(host, 0, 9, "slot ID", true) else {
        return;
    };
    ready(host, "SLOT:", slot);
    let Some(count) = recv_param(host, 1, MAX_SECTORS, "sector count", false) else {
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

/// Transfer v2 (the two-stage boot's): any length up to the caller's limit, in 512-byte
/// blocks, with a CRC-32 at the end. The host sends only after a line asks for it, so
/// the device can store each block (a card write) before the next arrives:
///
/// ```text
/// dev: [READY-V2]           host: length, u32 little-endian
/// dev: [LEN:n]              (or [ERR] Bad length: n, for 0 or over the limit)
/// dev: [READY-BLK:i]        host: the next min(512, rest) bytes   (per block)
/// dev: [READY-CRC]          host: CRC-32 of all n bytes, u32 little-endian
/// dev: [OK] n bytes CRC xxxxxxxx
/// ```
///
/// Any failure ends the exchange with an `[ERR] ...` line. tests/upload_v2_vectors/
/// holds transcripts that this side and tools/vux_tool.py's sender
/// (`send_upload_v2`) are both checked against (sim/emu/test_upload_v2.py).
pub const BLOCK: usize = 512;

/// Where the received bytes go: the PSRAM for a RAM boot, a file for an update.
pub trait UploadSink {
    /// Before the first block: false refuses `len` bytes.
    fn begin(&mut self, len: u32) -> bool;
    /// Block `index` (`BLOCK` bytes, the last one shorter); false stops the exchange.
    fn block(&mut self, index: u32, data: &[u8]) -> bool;
    /// After the last block: `ok` if every byte arrived and the CRC matched (commit),
    /// else after a failure (discard). false: the commit failed.
    fn finish(&mut self, ok: bool) -> bool;
}

/// A little-endian u32 from the host, or None on a timeout.
fn recv_u32(host: &mut impl Host) -> Option<u32> {
    let mut v = 0u32;
    for i in 0..4 {
        v |= (host.recv(BYTE_TIMEOUT_MS)? as u32) << (8 * i);
    }
    Some(v)
}

fn err(host: &mut impl Host, what: &str) {
    host.put_str("[ERR] ");
    host.put_str(what);
}

/// Run one v2 exchange into `sink`, accepting at most `max_len` bytes. Returns the
/// length on success; the reason has been reported on failure.
pub fn upload_v2(host: &mut impl Host, sink: &mut impl UploadSink, max_len: u32) -> Option<u32> {
    host.drain();
    host.put_str("[READY-V2]\n");
    let Some(len) = recv_u32(host) else {
        err(host, "Length timeout\n");
        return None;
    };
    if len == 0 || len > max_len {
        err(host, "Bad length: ");
        host.put_dec(len);
        host.put_str("\n");
        return None;
    }
    if !sink.begin(len) {
        err(host, "Cannot store ");
        host.put_dec(len);
        host.put_str(" bytes\n");
        return None;
    }
    host.put_str("[LEN:");
    host.put_dec(len);
    host.put_str("]\n");

    let mut buf = [0u8; BLOCK];
    let mut crc = 0xFFFF_FFFF;
    let mut rest = len;
    let mut index = 0;
    while rest > 0 {
        let n = if rest < BLOCK as u32 {
            rest as usize
        } else {
            BLOCK
        };
        ready(host, "BLK:", index);
        for (i, b) in buf[..n].iter_mut().enumerate() {
            match host.recv(BYTE_TIMEOUT_MS) {
                Some(v) => *b = v,
                None => {
                    err(host, "Timeout at block ");
                    host.put_dec(index);
                    host.put_str(", byte ");
                    host.put_dec(i as u32);
                    host.put_str("\n");
                    sink.finish(false);
                    return None;
                }
            }
        }
        if !sink.block(index, &buf[..n]) {
            err(host, "Store failed at block ");
            host.put_dec(index);
            host.put_str("\n");
            sink.finish(false);
            return None;
        }
        crc = crate::crc32_update(&buf[..n], crc);
        rest -= n as u32;
        index += 1;
    }
    crc ^= 0xFFFF_FFFF;

    host.put_str("[READY-CRC]\n");
    let Some(sent) = recv_u32(host) else {
        err(host, "CRC timeout\n");
        sink.finish(false);
        return None;
    };
    if sent != crc {
        err(host, "CRC mismatch: sent ");
        host.put_hex(sent);
        host.put_str(", received ");
        host.put_hex(crc);
        host.put_str("\n");
        sink.finish(false);
        return None;
    }
    if !sink.finish(true) {
        err(host, "Commit failed\n");
        return None;
    }
    host.put_str("[OK] ");
    host.put_dec(len);
    host.put_str(" bytes CRC ");
    host.put_hex(crc);
    host.put_str("\n");
    Some(len)
}
