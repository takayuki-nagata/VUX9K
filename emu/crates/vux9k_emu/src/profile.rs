// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Memory profiles: what the emulated board looks like.
//!
//! `Real` is the SoC as synthesized (soc/soc_ram.veryl, soc/soc_addr_decoder.veryl).
//! `Extended` keeps the same MMIO but larger memories, for software that doesn't fit
//! the real board (Zephyr bc); it is NOT real hardware. `IsaTest` is the flat 256 KB
//! RAM of sim/tb_hex_runner.veryl, used only to run riscv-tests.

/// Size of one memory, in bytes (always a power of two, so aliasing is a mask).
fn check_pow2(bytes: usize) -> usize {
    assert!(
        bytes.is_power_of_two() && bytes >= 4,
        "memory size {bytes} must be a power of two"
    );
    bytes
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    /// The Tang Nano 9K SoC: 16 KB I-RAM, 8 KB D-RAM.
    Real,
    /// Same MMIO as `Real`, with bigger memories (not real hardware).
    Extended {
        iram_bytes: usize,
        dram_bytes: usize,
    },
    /// riscv-tests harness: one flat RAM at 0 for code and data, no MMIO; a store to
    /// `TOHOST` ends the run.
    IsaTest,
}

impl Profile {
    /// Default extended sizes: room for the Zephyr bc image (~257 KB).
    pub const EXTENDED: Profile = Profile::Extended {
        iram_bytes: 512 * 1024,
        dram_bytes: 256 * 1024,
    };

    pub fn iram_bytes(self) -> usize {
        match self {
            Profile::Real => 16 * 1024,
            Profile::Extended { iram_bytes, .. } => check_pow2(iram_bytes),
            Profile::IsaTest => 256 * 1024,
        }
    }

    pub fn dram_bytes(self) -> usize {
        match self {
            Profile::Real => 8 * 1024,
            Profile::Extended { dram_bytes, .. } => check_pow2(dram_bytes),
            Profile::IsaTest => 0,
        }
    }

    /// True for anything that is not the real board.
    pub fn is_real(self) -> bool {
        self == Profile::Real
    }

    /// One-line description for banners and traces.
    pub fn describe(self) -> String {
        match self {
            Profile::Real => "real (Tang Nano 9K SoC: 16 KB I-RAM, 8 KB D-RAM)".to_string(),
            Profile::Extended {
                iram_bytes,
                dram_bytes,
            } => format!(
                "extended ({} KB I-RAM, {} KB D-RAM) -- NOT REAL HARDWARE",
                iram_bytes / 1024,
                dram_bytes / 1024
            ),
            Profile::IsaTest => "isa-test (flat 256 KB RAM, riscv-tests only)".to_string(),
        }
    }
}
