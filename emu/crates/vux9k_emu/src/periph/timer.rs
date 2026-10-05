// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Machine timer, soc/timer_core.veryl: 64-bit `mtime` counting every clock,
//! `mtimecmp`, and a registered `timer_irq = mtime >= mtimecmp`.
//!
//! Registers (addr[3:0]): 0x0/0x4 mtime lo/hi, 0x8/0xC mtimecmp lo/hi. A read returns
//! the value during the cycle the CPU presents the address (the load's EXECUTE
//! cycle; `data_out` is registered at its end). A write takes effect at the end of the
//! write cycle: writing mtime's low half drops that edge's +1, writing the high half
//! keeps the low half's +1 (no carry).

/// A value that changed at most once recently: `old` before cycle `from`, `new` from it.
#[derive(Clone, Copy, Debug)]
struct Stepped<T: Copy> {
    old: T,
    new: T,
    from: u64,
}

impl<T: Copy> Stepped<T> {
    fn new(v: T) -> Self {
        Stepped {
            old: v,
            new: v,
            from: 0,
        }
    }

    #[inline]
    fn at(&self, cycle: u64) -> T {
        if cycle >= self.from {
            self.new
        } else {
            self.old
        }
    }

    /// The value changes to `v` from `cycle` on (writes arrive in time order).
    fn set(&mut self, cycle: u64, v: T) {
        self.old = self.at(cycle.saturating_sub(1));
        self.new = v;
        self.from = cycle;
    }
}

#[derive(Clone, Debug)]
pub struct Timer {
    /// mtime during cycle k is `k + offset` (wrapping), for k after the last write.
    offset: Stepped<u64>,
    mtimecmp: Stepped<u64>,
}

impl Default for Timer {
    fn default() -> Self {
        Timer {
            offset: Stepped::new(0),
            mtimecmp: Stepped::new(u64::MAX),
        }
    }
}

impl Timer {
    /// mtime during cycle `c`.
    #[inline]
    pub fn mtime(&self, c: u64) -> u64 {
        c.wrapping_add(self.offset.at(c))
    }

    /// timer_irq during cycle `c` (registered: the comparison of cycle c-1).
    #[inline]
    pub fn irq(&self, c: u64) -> bool {
        c > 0 && self.mtime(c - 1) >= self.mtimecmp.at(c - 1)
    }

    pub fn read(&self, addr: u32, c: u64) -> u32 {
        match addr & 0xF {
            0x0 => self.mtime(c) as u32,
            0x4 => (self.mtime(c) >> 32) as u32,
            0x8 => self.mtimecmp.at(c) as u32,
            0xC => (self.mtimecmp.at(c) >> 32) as u32,
            _ => 0,
        }
    }

    /// Write at the end of cycle `c`.
    pub fn write(&mut self, addr: u32, v: u32, c: u64) {
        let lo = 0xFFFF_FFFFu64;
        let now = self.mtime(c);
        match addr & 0xF {
            0x0 => self.set_mtime(c + 1, (now & !lo) | v as u64),
            0x4 => self.set_mtime(c + 1, ((v as u64) << 32) | (now.wrapping_add(1) & lo)),
            0x8 => {
                let cmp = self.mtimecmp.at(c);
                self.mtimecmp.set(c + 1, (cmp & !lo) | v as u64);
            }
            0xC => {
                let cmp = self.mtimecmp.at(c);
                self.mtimecmp.set(c + 1, (cmp & lo) | ((v as u64) << 32));
            }
            _ => {}
        }
    }

    /// mtime is `value` during cycle `from` (and counts on from there).
    fn set_mtime(&mut self, from: u64, value: u64) {
        self.offset.set(from, value.wrapping_sub(from));
    }
}
