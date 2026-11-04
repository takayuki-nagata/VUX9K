// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! A CLINT-compatible block (SiFive's layout, Linux's `riscv,clint0`) at RV32 region 2
//! (`CLINT_BASE`, decoded on addr[15:2] and repeating every 64 KB across the region):
//! soc/timer_core.veryl (soc_pkg::RV_REGION_CLINT, `CLINT_*`), in the Real and
//! Linux profiles. msip is reset only by a power-on reset, not by the CPU's soft reset.
//!
//! Registers (32-bit words; a byte or halfword access hits its word): 0x0000 msip (bit 0,
//! the machine software interrupt, mip.MSIP), 0x4000/0x4004 mtimecmp lo/hi, 0xBFF8/0xBFFC
//! mtime lo/hi; everything else reads 0 and ignores writes. mtime and mtimecmp are the
//! machine timer's (crate::periph::Timer), with its timing: a read
//! returns the value during the load's EXECUTE cycle, a write takes effect at the end of
//! the store's write cycle, and mtime is writable as there. msip is timer_core's flop:
//! a store sets it at the end of its write cycle, and mip.MSIP shows it from the next.

use super::timer::{Reg, Stepped, Timer};

/// The block's base address (RV32 region 2).
pub const CLINT_BASE: u32 = 0x2000_0000;
pub const MSIP: u32 = 0x0000;
pub const MTIMECMP: u32 = 0x4000;
pub const MTIME: u32 = 0xBFF8;

#[derive(Clone, Debug)]
pub struct Clint {
    msip: Stepped<bool>,
}

impl Default for Clint {
    fn default() -> Self {
        Clint {
            msip: Stepped::new(false),
        }
    }
}

/// The machine timer's register at CLINT offset `off`.
fn timer_reg(off: u32) -> Option<Reg> {
    match off {
        MTIME => Some(Reg::MtimeLo),
        0xBFFC => Some(Reg::MtimeHi),
        MTIMECMP => Some(Reg::MtimecmpLo),
        0x4004 => Some(Reg::MtimecmpHi),
        _ => None,
    }
}

impl Clint {
    /// msip during cycle `c` (mip.MSIP).
    #[inline]
    pub fn msip(&self, c: u64) -> bool {
        self.msip.at(c)
    }

    /// Read in cycle `c` of the word holding `addr`.
    pub fn read(&self, addr: u32, timer: &Timer, c: u64) -> u32 {
        let off = addr & 0xFFFC;
        if off == MSIP {
            return self.msip(c) as u32;
        }
        timer_reg(off).map_or(0, |r| timer.read(r, c))
    }

    /// Write at the end of cycle `c`.
    pub fn write(&mut self, addr: u32, v: u32, timer: &mut Timer, c: u64) {
        let off = addr & 0xFFFC;
        if off == MSIP {
            self.msip.set(c + 1, v & 1 != 0);
        } else if let Some(r) = timer_reg(off) {
            timer.write(r, v, c);
        }
    }
}
