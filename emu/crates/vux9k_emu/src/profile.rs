// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Memory profiles: what the emulated board looks like.
//!
//! `Real` is the SoC as synthesized (soc/soc_rom.veryl, soc/soc_addr_decoder.veryl,
//! the wait-state window: 8 MB of PSRAM behind soc/soc_xmem_bridge.veryl).
//! `IsaTest` is the flat 256 KB RAM of sim/tb_hex_runner.veryl, used only to run
//! riscv-tests.
//! `Linux` is `Real` plus what a noMMU M-mode Linux kernel needs and the RTL doesn't have
//! yet: the A extension (`Soc::a_ext`), a
//! CLINT-compatible block at 0x2000_0000 and a liteuart register view of the UART
//! (crate::periph). It is ahead of the RTL: nothing proves it against the RTL, the
//! lockstep (`make sim-lockstep`) covers `Real` only, and its devices and cycle costs are
//! placeholders until the RTL has them. linux_workspace/dts/vux9k-linux-emu.dts describes it.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Profile {
    /// The Tang Nano 9K SoC: the 16 KB boot ROM, 8 MB PSRAM in the wait-state window.
    Real,
    /// riscv-tests harness: one flat RAM at 0 for code and data, no MMIO; a store to
    /// `TOHOST` ends the run.
    IsaTest,
    /// `Real` plus A, U-mode, the CLINT block and the liteuart view (emulator only).
    Linux,
}

impl Profile {
    /// The memory at 0: the boot ROM (Real, Linux: empty unless loaded), the flat RAM (IsaTest).
    pub fn rom_bytes(self) -> usize {
        match self {
            Profile::Real | Profile::Linux => 16 * 1024,
            Profile::IsaTest => 256 * 1024,
        }
    }

    /// The wait-state window at RV32 region 8 (the 8 MB PSRAM); the riscv-tests harness
    /// has none.
    pub fn xmem_bytes(self) -> usize {
        match self {
            Profile::Real | Profile::Linux => crate::soc::XMEM_WORDS * 4,
            Profile::IsaTest => 0,
        }
    }

    /// One-line description for banners and traces.
    pub fn describe(self) -> String {
        match self {
            Profile::Real => "real (Tang Nano 9K SoC: 16 KB boot ROM, 8 MB PSRAM)".to_string(),
            Profile::IsaTest => "isa-test (flat 256 KB RAM, riscv-tests only)".to_string(),
            Profile::Linux => {
                "linux (real + A, U-mode, CLINT, liteuart; emulator only, ahead of the RTL)"
                    .to_string()
            }
        }
    }
}
