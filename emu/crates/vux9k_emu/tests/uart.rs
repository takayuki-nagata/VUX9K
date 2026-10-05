// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! UART: TX bit-timer phase and pacing, RX push timing, FIFO limits, sticky error
//! flags and their clear, TX only from register 0x0, and the RX interrupt.

use vux9k_emu::periph::uart::{FIFO_DEPTH, FRAME};
use vux9k_emu::periph::Uart;
use vux9k_emu::{Profile, Soc};

const STATUS: u32 = 0x4000_0004;
const DATA: u32 = 0x4000_0000;

#[test]
fn tx_waits_for_the_bit_timer_then_paces_one_frame_per_byte() {
    let mut u = Uart::default();
    u.write(DATA, 0x41, 6);
    u.write(DATA, 0x42, 9);
    u.advance(10_000);
    // uart_tx leaves RST at the alarm in cycle 155: pop at 156, load at 311
    assert_eq!(u.tx_log(), &[(0x41, 312), (0x42, 312 + FRAME)]);
    assert_eq!(u.tx_complete_by(312 + FRAME), 1);
}

#[test]
fn tx_from_idle_starts_at_the_next_alarm() {
    let mut u = Uart::default();
    u.write(DATA, 0x55, 1000); // pop at 1001, load at the alarm in 1091
    u.advance(2000);
    assert_eq!(u.tx_log(), &[(0x55, 1092)]);
}

#[test]
fn only_register_0_transmits_and_a_full_fifo_drops_writes() {
    let mut u = Uart::default();
    u.write(0x4000_0004, 0x99, 5);
    for i in 0..(FIFO_DEPTH as u64 + 3) {
        u.write(DATA, i as u32, 10 + i);
    }
    // One byte leaves the FIFO at 156; everything written before that beyond 32 is lost
    assert_eq!(u.read(STATUS, 100) & 2, 2, "tx_full");
    u.advance(156 + 34 * FRAME);
    let sent: Vec<u8> = u.tx_log().iter().map(|&(b, _)| b).collect();
    assert_eq!(sent, (0..FIFO_DEPTH as u8).collect::<Vec<_>>());
}

#[test]
fn rx_byte_is_readable_1486_cycles_after_its_start_bit() {
    let mut u = Uart::default();
    u.host_send(&[0x5A, 0xA5], 100, false);
    assert!(!u.rx_pending(100 + 1485));
    assert!(u.rx_pending(100 + 1486));
    assert_eq!(u.read(STATUS, 100 + 1486), 0, "data, no errors");
    assert_eq!(u.read(DATA, 3000), 0x5A);
    assert!(!u.rx_pending(3001), "popped at the end of the read cycle");
    assert_eq!(u.read(DATA, 3001), 0, "empty reads 0");
    assert_eq!(u.host_send_done(), 100 + FRAME + 1486);
    assert_eq!(u.read(DATA, 100 + FRAME + 1486), 0xA5);
}

#[test]
fn overrun_and_frame_error_are_sticky_until_a_status_read() {
    let mut u = Uart::default();
    let bytes: Vec<u8> = (0..=FIFO_DEPTH as u8).collect();
    u.host_send(&bytes, 0, false);
    let done = u.host_send_done();
    assert_eq!(u.read(STATUS, done), 0b0100, "overrun, not empty");
    assert_eq!(u.read(STATUS, done + 1), 0, "cleared by the previous read");
    for i in 0..FIFO_DEPTH as u32 {
        assert_eq!(
            u.read(DATA, done + 2 + i as u64),
            i,
            "the 33rd byte was dropped"
        );
    }
    u.host_send(&[0x33], done + 100, true);
    let done = u.host_send_done();
    assert_eq!(
        u.read(DATA, done),
        0x33,
        "a bad stop bit still delivers the byte"
    );
    assert_eq!(u.read(STATUS, done + 1), 0b1001);
    assert_eq!(u.read(STATUS, done + 2), 0b0001);
}

#[test]
fn an_error_in_the_clearing_cycle_survives_the_clear() {
    let mut u = Uart::default();
    u.host_send(&[1], 0, true);
    let push = u.host_send_done() - 1;
    u.read(STATUS, push); // clr_err in the same cycle as rdy && frame_err
    assert_eq!(u.read(STATUS, push + 1), 0b1000);
}

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
fn csrs(csr: u32, rs1: u32) -> u32 {
    (csr << 20) | (rs1 << 15) | (2 << 12) | 0x73
}
const J_SELF: u32 = 0x0000_006F;

fn load(words: &[u32]) -> Soc {
    let mut soc = Soc::new(Profile::Real);
    let bytes: Vec<u8> = words.iter().flat_map(|w| w.to_le_bytes()).collect();
    soc.load_iram(0, &bytes);
    soc
}

#[test]
fn cpu_store_reaches_tx_at_its_mem_wait_edge() {
    // sw fetched in cycle 4: MEM_WAIT edge 6
    let mut soc = load(&[lui(1, 0x40000), addi(2, 0, 0x41), sw(2, 1, 0), J_SELF]);
    soc.run(3000);
    assert_eq!(soc.periph.uart.tx_log(), &[(0x41, 312)]);
}

#[test]
fn rx_data_raises_the_external_interrupt_until_read() {
    let handler = 0x80;
    let mut words = vec![
        addi(1, 0, handler),
        csrw(0x305, 1),     // mtvec
        lui(1, 1),          // 0x1000
        addi(1, 1, -0x800), // 0x800: MEIE
        csrs(0x304, 1),
        addi(2, 0, 8),
        csrw(0x300, 2), // mstatus.MIE
        J_SELF,         // at 0x1C
    ];
    words.resize(handler as usize / 4, 0x13);
    words.extend([lui(3, 0x40000), lw(4, 3, 0), lw(5, 3, 4), J_SELF]);
    let mut soc = load(&words);
    soc.periph.uart.host_send(&[0x7E], 0, false);
    soc.run(1000); // the byte is pushed at 1485
    assert_eq!(soc.pc, 0x1C, "nothing received yet");
    soc.run(3000);
    assert_eq!(soc.csr.mcause, 0x8000_000B);
    assert_eq!(soc.csr.mepc, 0x1C);
    assert_eq!(soc.regs[4], 0x7E);
    assert_eq!(soc.regs[5], 1, "empty again after the data read");
}

fn andi(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x13, imm, rs1, 7, rd)
}
fn bne(rs1: u32, rs2: u32, off: i32) -> u32 {
    let u = off as u32;
    ((u >> 12 & 1) << 31)
        | ((u >> 5 & 0x3F) << 25)
        | (rs2 << 20)
        | (rs1 << 15)
        | (1 << 12)
        | ((u >> 1 & 0xF) << 8)
        | ((u >> 11 & 1) << 7)
        | 0x63
}
fn jal0(off: i32) -> u32 {
    let u = off as u32;
    ((u >> 20 & 1) << 31) | ((u >> 1 & 0x3FF) << 21) | ((u >> 11 & 1) << 20) | (u & 0xF_F000) | 0x6F
}

/// `run_until_tx` as it was before it skipped the cycles in which the received output
/// can't grow: the output is looked at before every instruction.
fn run_until_tx_reference(
    soc: &mut Soc,
    needle: &[u8],
    from: usize,
    max_cycles: u64,
) -> Option<usize> {
    let end = soc.cycle.saturating_add(max_cycles);
    let mut searched = from;
    loop {
        let got = soc.uart_received().to_vec();
        if got.len() > searched {
            let start = searched.saturating_sub(needle.len()).max(from);
            if let Some(p) = got[start..]
                .windows(needle.len().max(1))
                .position(|w| w == needle)
            {
                return Some(start + p + needle.len());
            }
            searched = got.len();
        }
        if soc.cycle >= end {
            return None;
        }
        soc.step();
    }
}

#[test]
fn run_until_tx_stops_where_checking_every_instruction_would() {
    // Echo every received byte twice (so TX backs up behind RX), polling the status
    let echo = [
        lui(1, 0x40000),
        lw(2, 1, 4), // 0x04: wait for RX data
        andi(2, 2, 1),
        bne(2, 0, -8),
        lw(3, 1, 0),
        lw(2, 1, 4), // 0x14: wait for TX room
        andi(2, 2, 2),
        bne(2, 0, -8),
        sw(3, 1, 0),
        sw(3, 1, 0),
        jal0(-36), // back to 0x04
    ];
    let start = || {
        let mut soc = load(&echo);
        soc.periph
            .uart
            .host_send(b"Hello, emulator! 0123456789", 0, false);
        soc.periph
            .uart
            .host_send(b"abcdefghijklmnopqrstuvwxyz", 900_000, false);
        soc
    };
    // (needle, from, budget), one after the other on the same SoC as wait_for() calls go
    let calls: &[(&[u8], usize, u64)] = &[
        (b"H", 0, 1_000_000),
        (b"ee", 0, 1_000_000),
        (b"llll", 2, 1_000_000),
        (b"!!", 10, 1_000_000),
        (b"never", 0, 300_000), // budget runs out while bytes are still arriving
        (b"9", 0, 2_000_000),
        (b"z", 30, 3_000_000),
        (b"zz", 0, 50_000), // already there: found without stepping
        (b"x", 0, 100_000), // the line is idle: budget
    ];
    let (mut fast, mut slow) = (start(), start());
    for &(needle, from, budget) in calls {
        let got = fast.run_until_tx(needle, from, budget);
        let want = run_until_tx_reference(&mut slow, needle, from, budget);
        assert_eq!(got, want, "{:?}", std::str::from_utf8(needle));
        assert_eq!(
            (fast.cycle, fast.steps),
            (slow.cycle, slow.steps),
            "{:?}",
            std::str::from_utf8(needle)
        );
    }
    assert_eq!(fast.uart_received(), slow.uart_received());
    assert_eq!(
        fast.uart_received().len(),
        2 * 53,
        "everything echoed twice"
    );
}

#[test]
fn run_until_tx_stops_where_checking_every_instruction_would_after_idle_gaps() {
    // One byte at a time with gaps of 0 to ~4,000 cycles between them, so the
    // transmitter keeps going idle and restarting at every phase of its bit timer
    let slli = |rd, rs1, sh| i(0x13, sh, rs1, 1, rd);
    let add = |rd: u32, rs1: u32, rs2: u32| (rs2 << 20) | (rs1 << 15) | (rd << 7) | 0x33;
    let prog = [
        lui(1, 0x40000),
        addi(6, 0, 0),  // k
        andi(7, 6, 15), // 0x08
        addi(7, 7, 0x41),
        sw(7, 1, 0),
        slli(8, 6, 5),
        add(8, 8, 6),
        andi(8, 8, 0x3FF),
        addi(8, 8, 1),
        addi(8, 8, -1), // 0x24: delay loop
        bne(8, 0, -4),
        addi(6, 6, 1),
        jal0(-40), // back to 0x08
    ];
    let (mut fast, mut slow) = (load(&prog), load(&prog));
    let (mut at_fast, mut at_slow) = (0, 0);
    for k in 0..80u8 {
        let needle = [b'A' + (k & 15)];
        let got = fast.run_until_tx(&needle, at_fast, 20_000);
        let want = run_until_tx_reference(&mut slow, &needle, at_slow, 20_000);
        assert_eq!(got, want, "byte {k}");
        assert_eq!(fast.cycle, slow.cycle, "byte {k}");
        (at_fast, at_slow) = (got.unwrap(), want.unwrap());
    }
}

#[test]
fn a_byte_sent_after_a_quiet_spell_still_arrives() {
    let mut u = Uart::default();
    u.advance(10_000); // nothing pending: the UART has nothing to do from here on
    u.host_send(&[0x5A], 10_000, false);
    assert_eq!(u.rx_level(10_000 + 1485), 0);
    assert_eq!(u.rx_level(10_000 + 1486), 1);
    assert!(u.rx_pending(10_000 + 1486));
}
