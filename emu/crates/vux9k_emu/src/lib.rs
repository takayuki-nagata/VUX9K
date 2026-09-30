// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Emulator of the VUX9K SoC (Tang Nano 9K): the RV32I/Hack CPU, memories and
//! peripherals, modeled on the RTL under soc/ clock cycle by clock cycle.
#![forbid(unsafe_code)]

pub mod csr;
pub mod hexfile;
pub mod periph;
pub mod profile;
pub mod sdcard;
pub mod soc;

pub use profile::Profile;
pub use soc::{Isa, Retire, Soc, Stop};
