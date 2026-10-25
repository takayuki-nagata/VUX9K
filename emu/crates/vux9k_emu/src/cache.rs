// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The wait-state window's caches (soc/soc_icache.veryl, soc/soc_dcache.veryl), which
//! decide how long a window fetch, load or store waits.
//!
//! Only which PSRAM words they hold is modeled (and which data lines are dirty), not their
//! contents: the memory `Soc::xmem` is what the CPU sees, the truth. In the RTL the PSRAM
//! behind a dirty data line is stale (the stored bytes live only in the line until it is
//! written back), but the CPU never reads it there: a load goes through the data cache,
//! and a fetch that misses the instruction cache first probes the data cache, which writes
//! the line back if it holds the fetched word dirty. Every window store also invalidates
//! the instruction-cache line at its index (soc_top's `inv`), so an instruction-cache line
//! always equals the memory too. Lines are keyed by the PSRAM word (`addr[22:2]`), so the
//! window's 8 MB aliases share them. Every rule mirrors the RTL:
//! - direct-mapped: the instruction cache `ICACHE_LINES` lines of one word (index
//!   `addr[13:2]`, tag `addr[22:14]`), the data cache `DCACHE_LINES` lines of
//!   `DCACHE_LINE_WORDS` words (line `addr[10:5]`, word `addr[4:2]`, tag `addr[22:11]`),
//!   write-back and write-allocate;
//! - a hit (fetch, load or store) costs nothing over a fetch or a load of the ROM; a
//!   store hit marks its line dirty and makes no bus access;
//! - a data miss (load or store, also a partial store) fills the line, `XMEM_LINE_FILL`
//!   cycles (soc.rs), after writing the victim back first if it is dirty,
//!   `XMEM_LINE_DIRTY` in all; a store then merges into the line (dirty);
//! - an instruction miss waits `XMEM_READ_LATENCY`, or `XMEM_FETCH_PROBE` when the data
//!   cache holds its word in a dirty line (the same tag at the fetch's data line), which
//!   is written back and stays valid, now clean; a dirty line of another tag there costs
//!   nothing;
//! - the power-on reset (soc_top's rst_n, also the reset button) empties both (they sweep
//!   their lines while the POR counts, before the CPU's first cycle) and drops the dirty
//!   lines: their stores never reach the PSRAM, so `power_on` puts back the words the
//!   PSRAM still holds. A soft reset keeps both, dirty lines included.
//!
//! `Soc::load_xmem` writes the PSRAM past the caches (as a loader on the pins would): a
//! word it writes that a data line holds reads its new value here, the line's old one in
//! the RTL. It updates what a dirty line's PSRAM holds, so a later `power_on` keeps it.
//!
//! The RTL's guards change nothing here: a read-ahead that is stale (the cycle after one
//! outside IDLE, or after a write to the line or word being read) or at another word
//! would add a cycle, but the CPU never produces one. A data access's read-ahead is at its
//! address from the cycle before its lookup (RV32: data_addr computed in EXECUTE and held
//! in MEM_WAIT; Hack: A, a register, from FETCH for an `M=` stored in EXECUTE). The cache
//! writes in a store hit's cycle, which is the store's last (RV32 MEM_WAIT, Hack EXECUTE
//! or HACK_WB), and leaves IDLE only for a miss, which completes by a replay as a hit, or
//! for a probe, which ends before the fetch's own R cycles: the next lookup is at least a
//! FETCH (and, for a load, an EXECUTE) later, never right after a write or a cycle outside
//! IDLE. Within one instruction, Hack's AM=M+1 stores in HACK_WB right after its load
//! completed as a hit, a cycle with no write, at the same word: the store hits.

use crate::soc::{XMEM_FETCH_PROBE, XMEM_LINE_DIRTY, XMEM_LINE_FILL, XMEM_READ_LATENCY};

/// Lines of the instruction cache (soc_icache's LINES).
pub const ICACHE_LINES: usize = 4096;
/// Lines of the data cache (soc_pkg::DCACHE_LINES).
pub const DCACHE_LINES: usize = 64;
/// Words in a data-cache line (soc_dcache's 8).
pub const DCACHE_LINE_WORDS: usize = 8;

/// What the caches did since the power-on reset (for measurements; the RTL counts nothing).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CacheStats {
    pub fetch_hits: u64,
    pub fetch_misses: u64,
    pub load_hits: u64,
    pub load_misses: u64,
    /// Window stores, hits and misses.
    pub stores: u64,
    pub store_hits: u64,
    pub store_misses: u64,
    /// Data misses whose victim was dirty (written back before the fill).
    pub writebacks: u64,
    /// Instruction misses whose probe wrote a dirty data line back.
    pub probe_writebacks: u64,
}

/// A valid data-cache line.
#[derive(Clone, Copy)]
struct DLine {
    tag: u32,
    dirty: bool,
}

pub struct Caches {
    /// Per instruction-cache line, the tag of the word it holds.
    itag: Vec<Option<u32>>,
    /// Per data-cache line, the tag of the words it holds and whether it is dirty.
    dline: [Option<DLine>; DCACHE_LINES],
    /// Per dirty data line, the words the PSRAM holds behind it (the memory's words when
    /// the line became dirty).
    behind: [[u32; DCACHE_LINE_WORDS]; DCACHE_LINES],
    pub stats: CacheStats,
}

impl Default for Caches {
    fn default() -> Self {
        Caches {
            itag: vec![None; ICACHE_LINES],
            dline: [None; DCACHE_LINES],
            behind: [[0; DCACHE_LINE_WORDS]; DCACHE_LINES],
            stats: CacheStats::default(),
        }
    }
}

/// The data line and tag of PSRAM word `word`.
#[inline]
fn dline_of(word: usize) -> (usize, u32) {
    let line = word / DCACHE_LINE_WORDS;
    (line % DCACHE_LINES, (line / DCACHE_LINES) as u32)
}

impl Caches {
    /// A window fetch of PSRAM word `word` (`addr[22:2]`): the cycles its FETCH waits. A
    /// miss probes the data cache (writing a dirty line holding the word back) and fills
    /// the line.
    pub fn fetch(&mut self, word: usize) -> u32 {
        let (line, tag) = (word % ICACHE_LINES, (word / ICACHE_LINES) as u32);
        if self.itag[line] == Some(tag) {
            self.stats.fetch_hits += 1;
            return 0;
        }
        self.stats.fetch_misses += 1;
        self.itag[line] = Some(tag);
        let (dl, dtag) = dline_of(word);
        match &mut self.dline[dl] {
            Some(l) if l.tag == dtag && l.dirty => {
                l.dirty = false;
                self.stats.probe_writebacks += 1;
                XMEM_FETCH_PROBE
            }
            _ => XMEM_READ_LATENCY,
        }
    }

    /// A window load (`store` false) or store of PSRAM word `word`: the cycles it waits. A
    /// miss fills the line, writing a dirty victim back first; a store marks it dirty and
    /// invalidates the instruction-cache line at its index. `mem` is the memory before the
    /// store (what the PSRAM holds behind a clean line).
    pub fn access(&mut self, word: usize, store: bool, mem: &[u32]) -> u32 {
        let (line, tag) = dline_of(word);
        let wait = match self.dline[line] {
            Some(l) if l.tag == tag => 0,
            Some(DLine { dirty: true, .. }) => {
                self.stats.writebacks += 1;
                XMEM_LINE_DIRTY
            }
            _ => XMEM_LINE_FILL,
        };
        if wait != 0 {
            self.dline[line] = Some(DLine { tag, dirty: false });
        }
        let s = &mut self.stats;
        match (store, wait == 0) {
            (false, true) => s.load_hits += 1,
            (false, false) => s.load_misses += 1,
            (true, true) => s.store_hits += 1,
            (true, false) => s.store_misses += 1,
        }
        if store {
            s.stores += 1;
            let l = self.dline[line].as_mut().expect("a store's line is valid");
            if !l.dirty {
                l.dirty = true;
                let base = word & !(DCACHE_LINE_WORDS - 1);
                self.behind[line].copy_from_slice(&mem[base..base + DCACHE_LINE_WORDS]);
            }
            self.itag[word % ICACHE_LINES] = None;
        }
        wait
    }

    /// PSRAM word `word` written past the caches (`Soc::load_xmem`) with `value`: a dirty
    /// line holding it now has that value behind it.
    pub fn psram_written(&mut self, word: usize, value: u32) {
        let (line, tag) = dline_of(word);
        if let Some(DLine {
            tag: t,
            dirty: true,
        }) = self.dline[line]
        {
            if t == tag {
                self.behind[line][word % DCACHE_LINE_WORDS] = value;
            }
        }
    }

    /// The power-on reset: both caches empty, the dirty lines' stores lost (the PSRAM's
    /// words put back into `mem`).
    pub fn power_on(&mut self, mem: &mut [u32]) {
        for (line, l) in self.dline.iter().enumerate() {
            if let Some(DLine { tag, dirty: true }) = *l {
                let base = (tag as usize * DCACHE_LINES + line) * DCACHE_LINE_WORDS;
                mem[base..base + DCACHE_LINE_WORDS].copy_from_slice(&self.behind[line]);
            }
        }
        *self = Caches::default();
    }
}
