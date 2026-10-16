// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The wait-state window's instruction cache (soc/soc_icache.veryl), which decides
//! whether a window fetch waits.
//!
//! Only which PSRAM words it holds is modeled, not their contents: the CPU is the only
//! writer of the PSRAM and every window store invalidates its line, so a line always
//! equals the memory. Lines are keyed by the PSRAM word (`addr[22:2]`), so the window's
//! 8 MB aliases share them. Every rule mirrors the RTL:
//! - direct-mapped, one word a line, `ICACHE_LINES` lines: index `addr[11:2]`, tag
//!   `addr[22:12]`;
//! - a hit costs nothing over an I-RAM fetch, a miss waits as an uncached fetch did and
//!   fills the line;
//! - a window store invalidates the line at its index, whatever its tag (soc_top's `inv`);
//! - the power-on reset empties it (soc_icache sweeps its lines while the POR counts, before
//!   the CPU's first cycle); a soft reset keeps it.
//!   The RTL's guards (a miss stays one until the bridge answers; a read that collided with
//!   a write to its line misses) change nothing here: with the CPU's timing they only ever
//!   turn into misses what a line just invalidated, or a cycle that isn't a fetch, would miss
//!   anyway.

/// Lines of the instruction cache (soc_icache's LINES).
pub const ICACHE_LINES: usize = 1024;

/// What the caches did since the power-on reset (for measurements; the RTL counts nothing).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CacheStats {
    pub fetch_hits: u64,
    pub fetch_misses: u64,
}

pub struct Caches {
    /// Per instruction-cache line, the tag of the word it holds.
    itag: Vec<Option<u32>>,
    pub stats: CacheStats,
}

impl Default for Caches {
    fn default() -> Self {
        Caches {
            itag: vec![None; ICACHE_LINES],
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

    /// A window store to PSRAM word `word`: invalidates its instruction-cache line.
    pub fn store(&mut self, word: usize) {
        self.itag[word % ICACHE_LINES] = None;
    }
}
