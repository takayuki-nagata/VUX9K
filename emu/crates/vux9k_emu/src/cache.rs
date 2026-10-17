// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The wait-state window's caches (soc/soc_icache.veryl, soc/soc_dcache.veryl), which
//! decide whether a window fetch or load waits.
//!
//! Only which PSRAM words they hold is modeled, not their contents: the CPU is the only
//! writer of the PSRAM, the data cache is write-through and keeps a stored word's line
//! current or drops it, and every window store invalidates its instruction-cache line, so
//! a line always equals the memory. Lines are keyed by the PSRAM word (`addr[22:2]`), so
//! the window's 8 MB aliases share them. Every rule mirrors the RTL:
//! - direct-mapped, one word a line: the instruction cache `ICACHE_LINES` lines (index
//!   `addr[13:2]`, tag `addr[22:14]`), the data cache `DCACHE_LINES` (index `addr[10:2]`,
//!   tag `addr[22:11]`);
//! - a hit costs nothing over a fetch or a load of the ROM, a miss waits as an uncached
//!   access did and fills the line;
//! - a store always waits (write-through); in the data cache a hit keeps the line (its
//!   bytes merged), a full-word miss allocates it, a partial miss invalidates the line at
//!   its index whatever it holds; in the instruction cache it invalidates the line at its
//!   index (soc_top's `inv`);
//! - the power-on reset empties both (they sweep their lines while the POR counts, before
//!   the CPU's first cycle); a soft reset keeps them.
//!   The RTL's guards (a miss stays one until the bridge answers; a read that collided with
//!   a write to its line misses) change nothing here: with the CPU's timing they only ever
//!   turn into misses what a line just invalidated, or a cycle that isn't an access, would
//!   miss anyway.

/// Lines of the instruction cache (soc_icache's LINES).
pub const ICACHE_LINES: usize = 4096;
/// Lines of the data cache (soc_dcache's LINES).
pub const DCACHE_LINES: usize = 512;

/// What the caches did since the power-on reset (for measurements; the RTL counts nothing).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CacheStats {
    pub fetch_hits: u64,
    pub fetch_misses: u64,
    pub load_hits: u64,
    pub load_misses: u64,
    pub stores: u64,
}

pub struct Caches {
    /// Per instruction-cache line, the tag of the word it holds.
    itag: Vec<Option<u32>>,
    /// Per data-cache line, the tag of the word it holds.
    dtag: Vec<Option<u32>>,
    pub stats: CacheStats,
}

impl Default for Caches {
    fn default() -> Self {
        Caches {
            itag: vec![None; ICACHE_LINES],
            dtag: vec![None; DCACHE_LINES],
            stats: CacheStats::default(),
        }
    }
}

impl Caches {
    /// A window fetch of PSRAM word `word` (`addr[22:2]`): whether it hits. A miss fills
    /// the line.
    pub fn fetch(&mut self, word: usize) -> bool {
        let (line, tag) = (word % ICACHE_LINES, (word / ICACHE_LINES) as u32);
        let hit = self.itag[line] == Some(tag);
        if hit {
            self.stats.fetch_hits += 1;
        } else {
            self.stats.fetch_misses += 1;
            self.itag[line] = Some(tag);
        }
        hit
    }

    /// A window load of PSRAM word `word`: whether it hits. A miss fills the line.
    pub fn load(&mut self, word: usize) -> bool {
        let (line, tag) = (word % DCACHE_LINES, (word / DCACHE_LINES) as u32);
        let hit = self.dtag[line] == Some(tag);
        if hit {
            self.stats.load_hits += 1;
        } else {
            self.stats.load_misses += 1;
            self.dtag[line] = Some(tag);
        }
        hit
    }

    /// A window store to PSRAM word `word` with byte enables `be`.
    pub fn store(&mut self, word: usize, be: u8) {
        self.stats.stores += 1;
        let (line, tag) = (word % DCACHE_LINES, (word / DCACHE_LINES) as u32);
        if self.dtag[line] != Some(tag) {
            self.dtag[line] = if be == 0b1111 { Some(tag) } else { None };
        }
        self.itag[word % ICACHE_LINES] = None;
    }
}
