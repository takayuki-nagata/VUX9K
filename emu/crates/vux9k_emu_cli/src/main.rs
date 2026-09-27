// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! `vux9k-emu`: run a program on the VUX9K emulator.
//!
//! Usage: vux9k-emu [--profile real|extended|isa-test] [--cycles N] <image.bin>
//! The image is loaded into I-RAM at address 0 and run from reset.

use std::process::ExitCode;

use vux9k_emu::{Profile, Soc, Stop};

fn usage() -> ExitCode {
    eprintln!("usage: vux9k-emu [--profile real|extended|isa-test] [--cycles N] <image.bin>");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let mut profile = Profile::Real;
    let mut cycles: u64 = 10_000_000;
    let mut image = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--profile" => {
                profile = match args.next().as_deref() {
                    Some("real") => Profile::Real,
                    Some("extended") => Profile::EXTENDED,
                    Some("isa-test") => Profile::IsaTest,
                    _ => return usage(),
                }
            }
            "--cycles" => match args.next().and_then(|v| v.parse().ok()) {
                Some(n) => cycles = n,
                None => return usage(),
            },
            s if s.starts_with('-') => return usage(),
            s => image = Some(s.to_string()),
        }
    }
    let Some(image) = image else { return usage() };
    let bytes = match std::fs::read(&image) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("vux9k-emu: {image}: {e}");
            return ExitCode::from(1);
        }
    };
    eprintln!("vux9k-emu: profile {}", profile.describe());
    let mut soc = Soc::new(profile);
    soc.load_iram(0, &bytes);
    let stop = soc.run(cycles);
    eprintln!(
        "vux9k-emu: {stop:?} after {} cycles, {} instructions, pc={:#010x}",
        soc.cycle, soc.steps, soc.pc
    );
    match stop {
        Stop::ToHost(1) => ExitCode::SUCCESS,
        Stop::ToHost(_) => ExitCode::from(1),
        Stop::Budget => ExitCode::SUCCESS,
    }
}
