// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! UART: TX bit-timer phase and pacing, RX push timing, FIFO limits, a full RX FIFO and a
//! low stop bit leaving no trace in any register, liteuart's registers (TX only from RXTX,
//! EV_ENABLE, the 256-byte repeat), the interrupt, and the view before liteuart's
//! (`Soc::v03_mmio`).

use vux9k_emu::periph::uart::{FIFO_DEPTH, FRAME};
use vux9k_emu::periph::Uart;
use vux9k_emu::{Profile, Soc};

const DATA: u32 = 0x4000_0000; // RXTX
const TXFULL: u32 = 0x4000_0004;
const RXEMPTY: u32 = 0x4000_0008;
const EV_STATUS: u32 = 0x4000_000C;
const EV_PENDING: u32 = 0x4000_0010;
const EV_ENABLE: u32 = 0x4000_0014;
const TXEMPTY: u32 = 0x4000_0018;
const RXFULL: u32 = 0x4000_001C;

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
fn only_rxtx_transmits_and_a_full_fifo_drops_writes() {
    let mut u = Uart::default();
    for (k, reg) in [
        TXFULL,
        RXEMPTY,
        EV_STATUS,
        EV_PENDING,
        TXEMPTY,
        RXFULL,
        0x4000_0020,
    ]
    .into_iter()
    .enumerate()
    {
        u.write(reg, 0x99, 1 + k as u64);
    }
    assert_eq!(u.read(TXEMPTY, 9), 1, "nothing pushed");
    for i in 0..(FIFO_DEPTH as u64 + 3) {
        u.write(DATA, i as u32, 10 + i);
    }
    // One byte leaves the FIFO at 156; everything written before that beyond 32 is lost
    assert_eq!(u.read(TXFULL, 100), 1);
    assert_eq!(u.read(EV_STATUS, 100), 0, "no TX event while full");
    assert_eq!(u.read(TXEMPTY, 100), 0);
    u.advance(156 + 34 * FRAME);
    let sent: Vec<u8> = u.tx_log().iter().map(|&(b, _)| b).collect();
    assert_eq!(sent, (0..FIFO_DEPTH as u8).collect::<Vec<_>>());
}

#[test]
fn rx_byte_is_readable_1486_cycles_after_its_start_bit() {
    let mut u = Uart::default();
    u.host_send(&[0x5A, 0xA5], 100, false);
    assert_eq!(u.read(RXEMPTY, 100 + 1485), 1);
    assert!(!u.rx_pending(100 + 1485));
    assert!(u.rx_pending(100 + 1486));
    assert_eq!(u.read(RXEMPTY, 100 + 1486), 0);
    assert_eq!(u.read(DATA, 3000), 0x5A);
    assert!(!u.rx_pending(3001), "popped at the end of the read cycle");
    assert_eq!(u.read(DATA, 3001), 0, "empty reads 0");
    assert_eq!(u.host_send_done(), 100 + FRAME + 1486);
    assert_eq!(u.read(DATA, 100 + FRAME + 1486), 0xA5);
}

#[test]
fn rx_byte_may_start_in_the_past_while_its_fifo_push_is_still_ahead() {
    let mut u = Uart::default();
    u.advance(2000);
    // Its push edge is the end of cycle 2000, the one the UART is in
    u.host_send(&[0x5A], 2000 - 1485, false);
    assert!(u.rx_pending(2001));
    assert_eq!(u.read(DATA, 2001), 0x5A);
}

#[test]
#[should_panic(expected = "already passed")]
fn rx_byte_whose_fifo_push_has_passed_is_refused() {
    let mut u = Uart::default();
    u.advance(2000);
    u.host_send(&[0x5A], 2000 - 1486, false);
}

#[test]
fn a_byte_into_a_full_rx_fifo_is_dropped_without_a_trace() {
    let mut u = Uart::default();
    let bytes: Vec<u8> = (0..=FIFO_DEPTH as u8).collect();
    u.host_send(&bytes, 0, false);
    let done = u.host_send_done();
    assert_eq!(u.rx_dropped(done), 1, "the 33rd byte");
    assert_eq!(u.read(RXFULL, done), 1);
    for (k, reg) in [TXFULL, RXEMPTY, EV_STATUS, EV_PENDING, EV_ENABLE, TXEMPTY]
        .into_iter()
        .enumerate()
    {
        let want = [0, 0, 3, 3, 0, 1][k]; // EV_PENDING: EV_ENABLE = 0 shows the levels
        assert_eq!(u.read(reg, done), want, "register {reg:#x}");
    }
    assert_eq!(
        u.rx_level(done + 2),
        FIFO_DEPTH,
        "no read but RXTX pops a byte"
    );
    for i in 0..FIFO_DEPTH as u32 {
        assert_eq!(
            u.read(DATA, done + 2 + i as u64),
            i,
            "the first 32 bytes, in order"
        );
    }
    assert_eq!(u.read(RXEMPTY, done + 2 + FIFO_DEPTH as u64), 1, "empty");
    assert_eq!(u.read(RXFULL, done + 2 + FIFO_DEPTH as u64), 0);
}

#[test]
fn a_frame_with_a_low_stop_bit_is_received_without_a_trace() {
    let mut u = Uart::default();
    u.host_send(&[0x33], 0, true);
    let push = u.host_send_done() - 1;
    assert_eq!(u.read(RXEMPTY, push), 1, "empty until its push edge");
    assert_eq!(u.read(RXEMPTY, push + 1), 0);
    assert_eq!(u.read(EV_STATUS, push + 1), 3);
    assert_eq!(u.read(DATA, push + 2), 0x33, "the byte is kept");
    assert_eq!(u.read(RXEMPTY, push + 3), 1);
    assert_eq!(u.rx_dropped(push + 3), 0);
}

#[test]
fn liteuart_registers() {
    let mut u = Uart::default();
    assert_eq!(u.read(TXFULL, 10), 0);
    assert_eq!(u.read(RXEMPTY, 10), 1);
    assert_eq!(u.read(EV_STATUS, 10), 1, "TX: the TX FIFO isn't full");
    assert_eq!(
        u.read(EV_PENDING, 10),
        1,
        "EV_ENABLE 0: EV_STATUS, as Linux's polled driver reads it"
    );
    assert_eq!(u.read(EV_ENABLE, 10), 0, "resets to 0");
    assert_eq!(u.read(TXEMPTY, 10), 1);
    assert_eq!(u.read(RXFULL, 10), 0);
    assert!(!u.irq(10), "no interrupt without EV_ENABLE");
    // EV_ENABLE: 2 bits, from the edge on
    u.write(EV_ENABLE, 0xFF, 20);
    assert_eq!(u.read(EV_ENABLE, 20), 0);
    assert!(!u.irq(20));
    assert_eq!(u.read(EV_ENABLE, 21), 3);
    assert_eq!(u.read(EV_PENDING, 21), 1, "TX enabled and pending");
    assert!(u.irq(21), "TX enabled and the TX FIFO not full");
    u.write(EV_ENABLE, 2, 30);
    assert!(!u.irq(31), "RX enabled, nothing received");
    // EV_PENDING ignores writes (levels: nothing to clear); with RX enabled, TX is hidden
    u.write(EV_PENDING, 3, 40);
    assert_eq!(u.read(EV_PENDING, 41), 0);
    assert_eq!(u.read(EV_STATUS, 41), 1, "the TX event stays in EV_STATUS");
    // RX: a byte arrives (1486 cycles after its start bit), RXTX pops it
    u.host_send(b"Z", 100, false);
    assert!(!u.irq(100 + 1485));
    assert!(u.irq(100 + 1486));
    assert_eq!(u.read(EV_PENDING, 2000), 2, "RX only: TX is not enabled");
    assert_eq!(u.read(EV_STATUS, 2000), 3);
    assert_eq!(u.read(DATA, 2000), b'Z' as u32);
    assert_eq!(
        u.read(RXEMPTY, 2001),
        1,
        "popped at the end of the read cycle"
    );
    assert!(!u.irq(2001));
    // Repeating every 256 bytes (addr[7:2]); 0x20-0xFC read 0; the page's other bits ignored
    assert_eq!(u.read(0x4000_0114, 2002), 2);
    assert_eq!(u.read(0x4000_0F14, 2002), 2);
    for off in (0x20..0x100).step_by(4) {
        assert_eq!(u.read(0x4000_0000 + off, 2002), 0, "offset {off:#x}");
    }
    // Byte offsets within a register are the register (byte enables are not looked at)
    assert_eq!(u.read(EV_ENABLE + 1, 2002), 2);
    u.write(EV_ENABLE + 3, 0x0101_0101, 2003);
    assert_eq!(u.read(EV_ENABLE, 2004), 1);
    // TX: RXTX writes transmit; 40 writes fill the FIFO (TXFULL, no TX event)
    for k in 0..40u64 {
        u.write(DATA + (k as u32 & 3), b'a' as u32 + k as u32, 3000 + k);
    }
    assert_eq!(u.read(TXFULL, 3040), 1);
    assert_eq!(u.read(EV_STATUS, 3040) & 1, 0);
    assert!(!u.irq(3040), "TX enabled, the TX FIFO full");
    u.advance(200_000);
    assert_eq!(&u.tx_bytes()[..3], b"abc");
    assert_eq!(
        u.tx_bytes().len(),
        33,
        "one in the transmitter, 32 in the FIFO"
    );
    assert_eq!(u.read(TXFULL, 200_000), 0);
    assert_eq!(u.read(TXEMPTY, 200_000), 1);
    assert!(u.irq(200_000));
}

#[test]
fn txempty_is_the_fifo_not_the_transmitter() {
    let mut u = Uart::default();
    u.write(DATA, 0x41, 1000); // popped at 1001 into uart_tx, sent from 1092
    assert_eq!(
        u.read(TXEMPTY, 1001),
        0,
        "in the FIFO during the cycle after the store"
    );
    assert_eq!(
        u.read(TXEMPTY, 1002),
        1,
        "in the transmitter, still sending"
    );
    assert!(u.tx_complete_by(1002) == 0);
}

#[test]
fn the_soft_reset_clears_ev_enable_only() {
    let mut u = Uart::default();
    u.write(EV_ENABLE, 3, 10);
    u.write(DATA, 0x41, 11);
    u.host_send(b"Q", 0, false);
    u.soft_reset(2000);
    assert_eq!(u.read(EV_ENABLE, 2000), 3);
    assert_eq!(u.read(EV_ENABLE, 2001), 0);
    assert!(!u.irq(2001));
    assert_eq!(
        u.read(DATA, 2001),
        b'Q' as u32,
        "the RX FIFO keeps its byte"
    );
    u.advance(5000);
    assert_eq!(u.tx_bytes(), b"A", "and TX goes on");
}

#[test]
fn the_view_before_liteuart() {
    let mut u = Uart::default();
    assert_eq!(u.legacy_read(0x4000_0004, 10), 1, "status: rx_empty");
    u.legacy_write(0x4000_0004, 0x42, 11); // no TX from the status
    u.legacy_write(0x4000_0010, 0x41, 12); // repeating every 16 bytes
    u.host_send(b"q", 0, false);
    assert!(u.rx_pending(1486));
    assert_eq!(
        u.legacy_read(0x4000_000C, 1486),
        0,
        "data waiting, TX not full"
    );
    assert_eq!(u.legacy_read(0x4000_0000, 1487), b'q' as u32);
    assert!(!u.rx_pending(1488));
    u.advance(5000);
    assert_eq!(u.tx_bytes(), b"A");
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
    soc.load_rom(0, &bytes);
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
fn rx_data_raises_the_external_interrupt_once_enabled_until_read() {
    let handler = 0x80;
    let mut words = vec![
        addi(1, 0, handler),
        csrw(0x305, 1),     // mtvec
        lui(1, 1),          // 0x1000
        addi(1, 1, -0x800), // 0x800: MEIE
        csrs(0x304, 1),
        addi(2, 0, 8),
        csrw(0x300, 2), // mstatus.MIE
        lui(3, 0x40000),
        addi(2, 0, 2),
        J_SELF,         // at 0x24
        sw(2, 3, 0x14), // at 0x28: EV_ENABLE = RX
        J_SELF,         // at 0x2C
    ];
    words.resize(handler as usize / 4, 0x13);
    words.extend([lw(4, 3, 0), lw(5, 3, 8), J_SELF]);
    let mut soc = load(&words);
    soc.periph.uart.host_send(&[0x7E], 0, false);
    soc.run(3000); // the byte is pushed at 1485, EV_ENABLE is 0
    assert_eq!(soc.pc, 0x24, "not enabled: nothing taken");
    soc.pc = 0x28;
    soc.run(100);
    assert_eq!(soc.csr.mcause, 0x8000_000B, "taken once enabled");
    assert_eq!(soc.csr.mepc, 0x2C);
    assert_eq!(soc.regs[4], 0x7E);
    assert_eq!(soc.regs[5], 1, "RXEMPTY again after the RXTX read");
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
    // Echo every received byte twice (so TX backs up behind RX), polling RXEMPTY/TXFULL
    let echo = [
        lui(1, 0x40000),
        lw(2, 1, 8), // 0x04: wait for RX data
        andi(2, 2, 1),
        bne(2, 0, -8),
        lw(3, 1, 0),
        lw(2, 1, 4), // 0x14: wait for TX room
        andi(2, 2, 1),
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

#[test]
fn the_cpu_soft_reset_clears_ev_enable() {
    // First pass: EV_ENABLE = 3, read back, soft reset; second pass (x7 set, registers
    // survive the soft reset): EV_ENABLE read into x6
    let prog = [
        lui(3, 0x40000),
        lw(6, 3, 0x14),
        bne(7, 0, 36), // 0x08: to 0x2C on the second pass
        addi(7, 0, 1),
        addi(2, 0, 3),
        sw(2, 3, 0x14),
        lw(8, 3, 0x14),
        lui(4, 0x40003),
        lui(9, 0xA),
        addi(9, 9, 0x55A),
        sw(9, 4, 0xC), // 0x28: soft reset
        J_SELF,        // 0x2C
    ];
    let mut soc = load(&prog);
    soc.run(500);
    assert_eq!(soc.pc, 0x2C);
    assert_eq!(soc.regs[7], 1, "the second pass");
    assert_eq!(soc.regs[8], 3, "enabled before the soft reset");
    assert_eq!(soc.regs[6], 0, "cleared by it");
}
