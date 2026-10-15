// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Memory profiles: what the emulated board looks like.
//!
//! `Real` is the SoC as synthesized (soc/soc_ram.veryl, soc/soc_addr_decoder.veryl,
//! the wait-state window: 8 MB of PSRAM behind soc/soc_xmem_bridge.veryl).
//! `IsaTest` is the flat 256 KB RAM of sim/tb_hex_runner.veryl, used only to run
//! riscv-tests.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    /// The Tang Nano 9K SoC: 16 KB I-RAM, 8 KB D-RAM, 8 MB PSRAM in the wait-state window.
    Real,
    /// riscv-tests harness: one flat RAM at 0 for code and data, no MMIO; a store to
    /// `TOHOST` ends the run.
    IsaTest,
}

impl Profile {
    pub fn iram_bytes(self) -> usize {
        match self {
            Profile::Real => 16 * 1024,
            Profile::IsaTest => 256 * 1024,
        }
    }

    pub fn dram_bytes(self) -> usize {
        match self {
            Profile::Real => 8 * 1024,
            Profile::IsaTest => 0,
        }
    }

    /// The wait-state window at RV32 region 8 (the 8 MB PSRAM); the riscv-tests harness
    /// has none.
    pub fn xmem_bytes(self) -> usize {
        match self {
            Profile::Real => crate::soc::XMEM_WORDS * 4,
            Profile::IsaTest => 0,
        }
    }

    /// One-line description for banners and traces.
    pub fn describe(self) -> String {
        match self {
            Profile::Real => {
                "real (Tang Nano 9K SoC: 16 KB I-RAM, 8 KB D-RAM, 8 MB PSRAM)".to_string()
            }
            Profile::IsaTest => "isa-test (flat 256 KB RAM, riscv-tests only)".to_string(),
        }
    }
}
