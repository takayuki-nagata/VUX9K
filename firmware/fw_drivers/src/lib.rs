// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The SoC's MMIO drivers (UART, GPIO, mtime, SD over SPI), shared by the firmware that
//! runs on the board. Logic that needs no MMIO belongs in fw_common, with a host test.

#![no_std]

pub mod gpio;
pub mod sdcard;
pub mod timer;
pub mod uart;
