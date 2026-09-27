// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Timer and GPIO through the CPU: MMIO reads/writes, the timer interrupt, soft reset
//! with and without a boot mode, LEDs and the button.

use vux9k_emu::{Profile, Soc};

fn i(op: u32, imm: i32, rs1: u32, f3: u32, rd: u32) -> u32 {
    ((imm as u32 & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
}
fn addi(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x13, imm, rs1, 0, rd)
}
fn lw(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x03, imm, rs1, 2, rd)
}
fn sw(rs2: u32, rs1: u32, imm: i32) -> u32 {
    let u = imm as u32;
    ((u >> 5 & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (2 << 12) | ((u & 0x1F) << 7) | 0x23
}
fn lui(rd: u32, imm20: u32) -> u32 {
    (imm20 << 12) | (rd << 7) | 0x37
}
fn csrw(csr: u32, rs1: u32) -> u32 {
    (csr << 20) | (rs1 << 15) | (1 << 12) | 0x73
}
const J_SELF: u32 = 0x0000_006F;

fn load(words: &[u32]) -> Soc {
    let mut soc = Soc::new(Profile::Real);
    let bytes: Vec<u8> = words.iter().flat_map(|w| w.to_le_bytes()).collect();
    soc.load_iram(0, &bytes);
    soc
}

#[test]
fn mtime_counts_clocks_and_reads_in_execute() {
    // lui x1, 0x40001 (timer); lw x2, 0(x1) is fetched at cycle 2, reads in cycle 3
    let mut soc = load(&[lui(1, 0x40001), lw(2, 1, 0), lw(3, 1, 0)]);
    soc.step();
    soc.step();
    soc.step();
    assert_eq!(soc.regs[2], 3);
    assert_eq!(soc.regs[3], 3 + 3, "the load before took 3 cycles");
}

#[test]
fn mtime_low_write_replaces_that_edges_increment() {
    // lui c0, addi c2, sw fetched at c4 (MEM_WAIT c6): mtime is 100 during c7, not 101
    let mut soc = load(&[
        lui(1, 0x40001),
        addi(2, 0, 100),
        sw(2, 1, 0),
        lw(3, 1, 4),
        lw(4, 1, 0),
    ]);
    for _ in 0..5 {
        soc.step();
    }
    assert_eq!(soc.regs[3], 0, "high half");
    // the second lw is fetched at c10 and reads in c11: 100 + (11 - 7)
    assert_eq!(soc.regs[4], 104);
}

#[test]
fn timer_interrupt_traps_to_mtvec() {
    let handler = 0x80;
    let mut words = vec![
        addi(1, 0, handler),
        csrw(0x305, 1), // mtvec
        lui(1, 0x40001),
        sw(0, 1, 0xC), // mtimecmp hi = 0
        addi(2, 0, 60),
        sw(2, 1, 8), // mtimecmp lo = 60
        addi(2, 0, 0x80),
        csrw(0x304, 2), // mie.MTIE
        addi(2, 0, 8),
        csrw(0x300, 2), // mstatus.MIE
        J_SELF,         // at 0x28: spin
    ];
    words.resize(handler as usize / 4, 0x13);
    words.push(J_SELF);
    let mut soc = load(&words);
    soc.run(200);
    assert_eq!(soc.pc, handler as u32);
    assert_eq!(soc.csr.mcause, 0x8000_0007);
    assert_eq!(soc.csr.mepc, 0x28, "taken on the spin loop");
}

#[test]
fn soft_reset_restarts_at_zero_with_the_given_isa() {
    // GPIO 0x8 = valid | Hack, then the reset key; x5 survives (the regfile has no reset)
    let mut soc = load(&[
        addi(5, 0, 55),
        lui(1, 0x40003),
        addi(2, 0, 0x100),
        sw(2, 1, 8),
        lui(3, 0xA),       // 0xA000
        addi(3, 3, 0x55A), // 0xA55A
        sw(3, 1, 0xC),     // soft reset (fetched at cycle 13, MEM_WAIT edge at 15)
        addi(6, 0, 1),     // never runs before the reset
    ]);
    for _ in 0..7 {
        soc.step();
    }
    assert_eq!(soc.pc, 0);
    assert!(
        !soc.riscv_mode,
        "register 0x8 selected Hack for this soft reset"
    );
    assert_eq!(
        soc.cycle,
        15 + 1 + 15,
        "the CPU fetches once the 15-cycle pulse ends"
    );
    assert_eq!((soc.regs[5], soc.regs[6]), (55, 0));
    assert_eq!(soc.csr.mcycle, 0, "CSRs reset");
}

#[test]
fn soft_reset_without_mode_auto_detects() {
    let mut soc = load(&[
        lui(1, 0x40003),
        lui(3, 0xA),
        addi(3, 3, 0x55A),
        sw(3, 1, 0xC),
        addi(6, 0, 1),
    ]);
    for _ in 0..4 {
        soc.step();
    }
    assert_eq!(soc.pc, 0);
    assert!(soc.riscv_mode);
    soc.step(); // lui again: detected as RV32
    assert!(soc.riscv_mode);
}

#[test]
fn leds_and_button() {
    let mut soc = load(&[
        lui(1, 0x40003),
        addi(2, 0, 0x2A),
        sw(2, 1, 0),
        lw(3, 1, 4),
        lw(4, 1, 4),
    ]);
    soc.periph.gpio.set_button(0, true);
    for _ in 0..4 {
        soc.step();
    }
    assert_eq!(soc.periph.gpio.led_pins(), !0x2A & 0x3F, "active-low pins");
    assert_eq!(soc.regs[3], 1, "pressed (after the 2-flop synchronizer)");
    soc.periph.gpio.set_button(soc.cycle, false);
    soc.step(); // fetched now, reads one cycle later: the release hasn't reached btn_sync[1]
    assert_eq!(soc.regs[4], 1);
}
