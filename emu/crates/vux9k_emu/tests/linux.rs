// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The Linux profile (emulator only, ahead of the RTL): the A extension, U-mode, the CLINT
//! block, the UART's liteuart view and `load_linux`; and that Real keeps none of them.

use vux9k_emu::periph::uart::Uart;
use vux9k_emu::soc::{
    LINUX_DTB_ADDR, LINUX_DTB_MAX, XMEM_FETCH_FILL, XMEM_LINE_DIRTY, XMEM_LINE_FILL,
};
use vux9k_emu::{Profile, Retire, Soc};

// ----- encoders ---------------------------------------------------------------------
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
fn csr(f3: u32, rd: u32, csr: u32, rs1: u32) -> u32 {
    (csr << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0x73
}
/// An A-extension instruction: funct5, aq/rl, rs2, rs1 (the address), rd.
fn amo(f5: u32, aqrl: u32, rs2: u32, rs1: u32, rd: u32) -> u32 {
    (f5 << 27) | (aqrl << 25) | (rs2 << 20) | (rs1 << 15) | (2 << 12) | (rd << 7) | 0x2F
}
const LR: u32 = 0x02;
const SC: u32 = 0x03;
const ECALL: u32 = 0x0000_0073;
const MRET: u32 = 0x3020_0073;
const WFI: u32 = 0x1050_0073;
const HANDLER: u32 = 0x100;

/// A Soc of `profile` whose trap vector is `HANDLER` (in the empty ROM).
fn new_soc(profile: Profile) -> Soc {
    let mut soc = Soc::new(profile);
    soc.csr.mtvec = HANDLER;
    soc
}

/// Execute `word` from ROM address 0 (whatever the privilege).
fn exec(soc: &mut Soc, word: u32) -> Retire {
    soc.load_rom(0, &word.to_le_bytes());
    soc.pc = 0;
    soc.step()
}

const A: u32 = 0x8000_0100;

// ----- the A extension --------------------------------------------------------------

#[test]
fn amo_ops_return_the_old_value_and_store_the_result() {
    let cases: [(u32, u32, u32, u32); 18] = [
        // (funct5, memory, rs2, memory after)
        (0x01, 5, 9, 9),                               // swap
        (0x00, 5, 9, 14),                              // add
        (0x00, 0xFFFF_FFFF, 2, 1),                     // add wraps
        (0x04, 0b1100, 0b1010, 0b0110),                // xor
        (0x0C, 0b1100, 0b1010, 0b1000),                // and
        (0x08, 0b1100, 0b1010, 0b1110),                // or
        (0x10, 0xFFFF_FFFE, 3, 0xFFFF_FFFE),           // min: -2 < 3
        (0x10, 3, 0xFFFF_FFFE, 0xFFFF_FFFE),           // min, the other way
        (0x14, 0xFFFF_FFFE, 3, 3),                     // max
        (0x14, 0x8000_0000, 0x7FFF_FFFF, 0x7FFF_FFFF), // max at the extremes
        (0x18, 0xFFFF_FFFE, 3, 3),                     // minu
        (0x18, 3, 0xFFFF_FFFE, 3),                     // minu, the other way
        (0x1C, 0xFFFF_FFFE, 3, 0xFFFF_FFFE),           // maxu
        (0x1C, 0x7FFF_FFFF, 0x8000_0000, 0x8000_0000), // maxu at the extremes
        (0x10, 7, 7, 7),                               // min, equal
        (0x01, 0xDEAD_BEEF, 0, 0),                     // swap in zero
        (0x00, 1, 0xFFFF_FFFF, 0),                     // add -1
        (0x08, 0, 0, 0),                               // or, nothing
    ];
    for (f5, mem, src, after) in cases {
        for aqrl in [0, 3] {
            let mut soc = new_soc(Profile::Linux);
            soc.load_xmem(A, &mem.to_le_bytes()).unwrap();
            soc.regs[1] = A;
            soc.regs[2] = src;
            let r = exec(&mut soc, amo(f5, aqrl, 2, 1, 3));
            assert_eq!(r.trap, None, "funct5 {f5:#x}");
            assert_eq!(soc.regs[3], mem, "funct5 {f5:#x}: rd = the old value");
            assert_eq!(
                soc.xmem_word(A),
                after,
                "funct5 {f5:#x}: {mem:#x} op {src:#x}"
            );
            assert_eq!(r.store, Some((A, after, 0b1111)));
        }
    }
    // rd = rs2: the old value lands in rd after rs2 was read
    let mut soc = new_soc(Profile::Linux);
    soc.load_xmem(A, &10u32.to_le_bytes()).unwrap();
    soc.regs[1] = A;
    soc.regs[2] = 5;
    exec(&mut soc, amo(0x00, 0, 2, 1, 2));
    assert_eq!((soc.regs[2], soc.xmem_word(A)), (10, 15));
}

#[test]
fn amo_cycles_and_caches_are_a_load_and_a_store() {
    let (rw, fill, dirty) = (XMEM_FETCH_FILL, XMEM_LINE_FILL, XMEM_LINE_DIRTY);
    let mut soc = new_soc(Profile::Linux);
    soc.regs[1] = A;
    let add = amo(0x00, 0, 0, 1, 3);
    assert_eq!(
        exec(&mut soc, add).cycles,
        5 + fill,
        "cold: a load miss, then the store hits the line"
    );
    assert_eq!(exec(&mut soc, add).cycles, 5, "the line stays: hits");
    assert_eq!(exec(&mut soc, amo(LR, 0, 0, 1, 3)).cycles, 3, "LR hits");
    assert_eq!(exec(&mut soc, amo(SC, 0, 0, 1, 3)).cycles, 3, "SC hits");
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses), (2, 1));
    assert_eq!((st.store_hits, st.store_misses), (3, 0));
    // Another tag on the line (2 KB up): the AMO's load evicts the dirty line. An SC whose
    // line went since its LR misses on its store: here the AMO dirtied the line in between
    soc.regs[1] = A + 0x800;
    assert_eq!(exec(&mut soc, add).cycles, 5 + dirty, "a dirty victim");
    soc.regs[1] = A;
    assert_eq!(exec(&mut soc, amo(LR, 0, 0, 1, 3)).cycles, 3 + dirty);
    soc.regs[1] = A + 0x800;
    assert_eq!(
        exec(&mut soc, add).cycles,
        5 + fill,
        "the LR's line was clean"
    );
    soc.regs[1] = A;
    assert_eq!(exec(&mut soc, amo(SC, 0, 0, 1, 3)).cycles, 3 + dirty);
    assert_eq!(soc.caches.stats.writebacks, 3);
    assert_eq!(
        exec(&mut soc, amo(SC, 0, 0, 1, 3)).cycles,
        2,
        "a failing SC ends in EXECUTE"
    );
    // Outside the window: no waits
    soc.regs[1] = 0x0000_0040;
    assert_eq!(exec(&mut soc, add).cycles, 5);
    // A store into the window evicts the instruction-cache line at its index, as SW does
    let mut soc = new_soc(Profile::Linux);
    soc.load_xmem(0x8000_0000, &addi(0, 0, 0).to_le_bytes())
        .unwrap();
    let fetch = |soc: &mut Soc| {
        soc.pc = 0x8000_0000;
        soc.step().cycles
    };
    assert_eq!(fetch(&mut soc), 2 + rw);
    assert_eq!(fetch(&mut soc), 2);
    soc.regs[1] = 0x8000_4000; // the same instruction-cache line, 16 KB up
    exec(&mut soc, add);
    assert_eq!(
        fetch(&mut soc),
        2 + rw,
        "the AMO's store invalidated the line"
    );
}

#[test]
fn lr_sc_reservation() {
    let b = A + 0x40;
    let mut soc = new_soc(Profile::Linux);
    soc.load_xmem(A, &7u32.to_le_bytes()).unwrap();
    soc.regs[1] = A;
    soc.regs[2] = b;
    soc.regs[4] = 0x55;
    // LR then SC to the same address: success (rd 0), stored
    exec(&mut soc, amo(LR, 2, 0, 1, 3));
    assert_eq!(soc.regs[3], 7);
    exec(&mut soc, amo(SC, 1, 4, 1, 5));
    assert_eq!((soc.regs[5], soc.xmem_word(A)), (0, 0x55));
    // Any SC clears the reservation: the second fails (rd 1), nothing stored
    soc.regs[4] = 0x66;
    let r = exec(&mut soc, amo(SC, 0, 4, 1, 5));
    assert_eq!((soc.regs[5], soc.xmem_word(A), r.store), (1, 0x55, None));
    // An SC to another address fails, and clears the reservation for the first too
    exec(&mut soc, amo(LR, 0, 0, 1, 3));
    exec(&mut soc, amo(SC, 0, 4, 2, 5));
    assert_eq!((soc.regs[5], soc.xmem_word(b)), (1, 0));
    exec(&mut soc, amo(SC, 0, 4, 1, 5));
    assert_eq!((soc.regs[5], soc.xmem_word(A)), (1, 0x55));
    // A trap clears it
    exec(&mut soc, amo(LR, 0, 0, 1, 3));
    let r = exec(&mut soc, ECALL);
    assert_eq!(r.trap, Some(11));
    exec(&mut soc, amo(SC, 0, 4, 1, 5));
    assert_eq!((soc.regs[5], soc.xmem_word(A)), (1, 0x55));
    // Without one, an SC fails from reset
    let mut soc = new_soc(Profile::Linux);
    soc.regs[1] = A;
    exec(&mut soc, amo(SC, 0, 0, 1, 5));
    assert_eq!(soc.regs[5], 1);
}

#[test]
fn misaligned_atomics_trap() {
    for off in [1, 2, 3] {
        for (word, cause) in [
            (amo(LR, 0, 0, 1, 3), 4),
            (amo(SC, 0, 2, 1, 3), 6),
            (amo(0x00, 0, 2, 1, 3), 6),
            (amo(0x01, 0, 2, 1, 3), 6),
        ] {
            let mut soc = new_soc(Profile::Linux);
            soc.load_xmem(A, &7u32.to_le_bytes()).unwrap();
            soc.regs[1] = A + off;
            soc.regs[2] = 9;
            soc.regs[3] = 0x1234;
            let r = exec(&mut soc, word);
            assert_eq!(r.trap, Some(cause), "{word:#010x} at +{off}");
            assert_eq!(
                (soc.csr.mcause, soc.csr.mtval, soc.csr.mepc),
                (cause, A + off, 0)
            );
            assert_eq!((soc.regs[3], soc.xmem_word(A), r.store), (0x1234, 7, None));
            assert_eq!(soc.pc, HANDLER);
        }
    }
}

#[test]
fn atomics_are_illegal_without_a_ext() {
    // Without A: opcode 0x2F is illegal (mtval = the word)
    for word in [
        amo(LR, 0, 0, 1, 3),
        amo(SC, 0, 2, 1, 3),
        amo(0x00, 0, 2, 1, 3),
    ] {
        let mut soc = new_soc(Profile::Real);
        soc.a_ext = false;
        soc.regs[1] = A;
        let r = exec(&mut soc, word);
        assert_eq!((r.trap, soc.csr.mtval), (Some(2), word));
    }
    // Linux: LR with rs2 != 0, other widths (funct3 3 = .D) and unknown funct5 are illegal
    for word in [
        amo(LR, 0, 1, 1, 3),
        amo(0x00, 0, 2, 1, 3) & !(7 << 12) | (3 << 12),
        amo(0x05, 0, 2, 1, 3),
        amo(0x1F, 0, 2, 1, 3),
    ] {
        let mut soc = new_soc(Profile::Linux);
        soc.regs[1] = A;
        let r = exec(&mut soc, word);
        assert_eq!(r.trap, Some(2), "{word:#010x}");
    }
    // The switch alone decides, and it stays across a reset
    let mut soc = new_soc(Profile::Real);
    soc.regs[1] = A;
    assert_eq!(exec(&mut soc, amo(0x00, 0, 0, 1, 3)).trap, None);
    soc.reset();
    let mut soc = new_soc(Profile::Linux);
    soc.a_ext = false;
    assert_eq!(exec(&mut soc, amo(0x00, 0, 0, 1, 3)).trap, Some(2));
}

// ----- misa, mstatus ------------------------------------------------------------------

#[test]
fn misa_and_mstatus_per_profile() {
    let read = |soc: &mut Soc, addr: u32| {
        exec(soc, csr(2, 5, addr, 0));
        soc.regs[5]
    };
    let mut real = new_soc(Profile::Real);
    assert_eq!(read(&mut real, 0x301), 0x4010_1101, "Real: RV32IMAU");
    assert_eq!(read(&mut real, 0x300), 0x1800, "MPP is M after reset");
    real.regs[6] = 0x0022_1888; // TW, MPRV, MPP = M, MPIE, MIE (bits 12:11 and above kept)
    exec(&mut real, csr(1, 0, 0x300, 6));
    assert_eq!(read(&mut real, 0x300), 0x0022_1888);
    let mut isa = new_soc(Profile::IsaTest);
    assert_eq!(read(&mut isa, 0x301), 0x4010_1101);
    let mut linux = new_soc(Profile::Linux);
    assert_eq!(read(&mut linux, 0x301), 0x4010_1101, "Linux: RV32IMAU");
    assert_eq!(read(&mut linux, 0x300), 0x1800, "MPP is M after reset");
    // Linux: MPP is WARL {U, M}: 3 is M, everything else U
    for (mpp, reads) in [(0, 0), (1, 0), (2, 0), (3, 0x1800)] {
        linux.regs[6] = mpp << 11;
        exec(&mut linux, csr(1, 0, 0x300, 6));
        assert_eq!(read(&mut linux, 0x300), reads, "MPP written {mpp}");
    }
    // mcounteren reads 0, ignores writes
    linux.regs[6] = 7;
    assert_eq!(exec(&mut linux, csr(1, 0, 0x306, 6)).trap, None);
    assert_eq!(read(&mut linux, 0x306), 0);
    assert_eq!(exec(&mut real, csr(2, 5, 0x306, 0)).trap, None);
}

// ----- U-mode -------------------------------------------------------------------------

/// Enter U-mode at `pc` through MRET with MPP = U.
fn enter_user(soc: &mut Soc, pc: u32) {
    soc.regs[6] = 0x1800;
    exec(soc, csr(3, 0, 0x300, 6)); // MPP = U
    soc.regs[6] = pc;
    exec(soc, csr(1, 0, 0x341, 6)); // mepc
    let r = exec(soc, MRET);
    assert_eq!(r.trap, None);
    assert!(soc.csr.user);
    assert_eq!(soc.pc, pc);
}

#[test]
fn mret_to_user_and_ecall_back() {
    let mut soc = new_soc(Profile::Linux);
    enter_user(&mut soc, 0x40);
    assert_eq!(soc.csr.read(0x300, 0, 0) & 0x1800, 0, "MRET leaves MPP = U");
    // Code in U runs; ecall from U is cause 8 with MPP = U, back in M
    soc.load_rom(0x40, &addi(7, 0, 3).to_le_bytes());
    soc.load_rom(0x44, &ECALL.to_le_bytes());
    soc.step();
    assert_eq!(soc.regs[7], 3);
    let r = soc.step();
    assert_eq!(r.trap, Some(8));
    assert_eq!((soc.csr.mepc, soc.pc, soc.csr.user), (0x44, HANDLER, false));
    assert_eq!(
        soc.csr.read(0x300, 0, 0) & 0x1800,
        0,
        "MPP = U: the trap came from U"
    );
    // ecall from M stays 11, and records MPP = M
    assert_eq!(exec(&mut soc, ECALL).trap, Some(11));
    assert_eq!(soc.csr.read(0x300, 0, 0) & 0x1800, 0x1800);
    // MRET with MPP = M stays in M (and leaves MPP = U)
    soc.regs[6] = 0x80;
    exec(&mut soc, csr(1, 0, 0x341, 6));
    exec(&mut soc, MRET);
    assert!(!soc.csr.user);
    assert_eq!(soc.pc, 0x80);
    assert_eq!(soc.csr.read(0x300, 0, 0) & 0x1800, 0);
}

#[test]
fn user_mode_denies_m_csrs_counters_and_mret() {
    for word in [
        csr(2, 5, 0x300, 0), // mstatus
        csr(2, 5, 0x341, 0), // mepc
        csr(2, 5, 0xB00, 0), // mcycle
        csr(2, 5, 0xF14, 0), // mhartid
        csr(2, 5, 0x7A0, 0), // tselect
        csr(2, 5, 0x306, 0), // mcounteren
        csr(2, 5, 0xC00, 0), // cycle: mcounteren.CY = 0
        csr(2, 5, 0xC01, 0), // time
        csr(2, 5, 0xC02, 0), // instret
        csr(2, 5, 0xC80, 0), // cycleh
        csr(2, 5, 0xC81, 0),
        csr(2, 5, 0xC82, 0),
        MRET,
    ] {
        let mut soc = new_soc(Profile::Linux);
        enter_user(&mut soc, 0);
        soc.regs[5] = 0x1234;
        let r = exec(&mut soc, word);
        assert_eq!(
            (r.trap, soc.csr.mtval),
            (Some(2), word),
            "{word:#010x} in U"
        );
        assert_eq!(soc.regs[5], 0x1234);
        assert!(!soc.csr.user);
        assert_eq!(soc.csr.read(0x300, 0, 0) & 0x1800, 0, "taken from U");
    }
    // WFI in U is a no-op, as in M
    let mut soc = new_soc(Profile::Linux);
    enter_user(&mut soc, 0);
    let r = exec(&mut soc, WFI);
    assert_eq!((r.trap, soc.pc, soc.csr.user), (None, 4, true));
    // In M the counters stay readable
    let mut soc = new_soc(Profile::Linux);
    assert_eq!(exec(&mut soc, csr(2, 5, 0xC00, 0)).trap, None);
}

#[test]
fn interrupts_are_on_in_user_mode_whatever_mie() {
    let mut soc = new_soc(Profile::Linux);
    soc.csr.mie = 1 << 7; // MTIE
                          // mtimecmp = 0 through the CLINT block: the timer interrupt is pending
    soc.regs[1] = 0x2000_4000;
    exec(&mut soc, sw(0, 1, 0));
    exec(&mut soc, sw(0, 1, 4));
    // In M with mstatus.MIE = 0: not taken
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, None);
    // In U, still MIE = 0: taken, MPP = U, MPIE = 0
    enter_user(&mut soc, 0x40);
    assert_eq!(soc.csr.mstatus & 0x8, 0);
    let r = soc.step();
    assert_eq!(r.trap, Some(0x8000_0007));
    assert_eq!((soc.csr.mepc, soc.csr.user), (0x40, false));
    assert_eq!(soc.csr.read(0x300, 0, 0) & 0x1888, 0);
    // Back in M (MIE = 0) it isn't taken again
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, None);
    // mret back to U takes it again at once
    soc.regs[6] = 0x40;
    exec(&mut soc, csr(1, 0, 0x341, 6));
    exec(&mut soc, MRET);
    assert!(soc.csr.user);
    assert_eq!(soc.step().trap, Some(0x8000_0007));
}

// ----- the CLINT block ------------------------------------------------------------------

#[test]
fn clint_registers_are_the_timer_and_msip() {
    let mut soc = new_soc(Profile::Linux);
    let load = |soc: &mut Soc, addr: u32| {
        soc.regs[1] = addr;
        exec(soc, lw(5, 1, 0));
        soc.regs[5]
    };
    let store = |soc: &mut Soc, addr: u32, v: u32| {
        soc.regs[1] = addr;
        soc.regs[2] = v;
        let r = exec(soc, sw(2, 1, 0));
        assert_eq!(r.cycles, 3, "an MMIO store's cost");
    };
    // mtime as read in EXECUTE (cycle + 1 here, mtime == cycle)
    soc.cycle = 0x1_2345_6789;
    let c = soc.cycle;
    assert_eq!(load(&mut soc, 0x2000_BFF8), (c + 1) as u32);
    assert_eq!(load(&mut soc, 0x2000_BFFC), 1);
    // mtimecmp written through the CLINT is the timer's (its old page at 0x4000_1008)
    store(&mut soc, 0x2000_4000, 0xAABB_CCDD);
    store(&mut soc, 0x2000_4004, 0x11);
    assert_eq!(load(&mut soc, 0x4000_1008), 0xAABB_CCDD);
    assert_eq!(load(&mut soc, 0x4000_100C), 0x11);
    assert_eq!(load(&mut soc, 0x2000_4004), 0x11);
    // Mirrored across region 2 (addr[15:0]); other offsets read 0
    assert_eq!(load(&mut soc, 0x2ABC_4000), 0xAABB_CCDD);
    assert_eq!(load(&mut soc, 0x2000_0004), 0);
    assert_eq!(load(&mut soc, 0x2000_8000), 0);
    // mtime is writable, as on the timer's page
    store(&mut soc, 0x2000_BFFC, 0);
    store(&mut soc, 0x2000_BFF8, 1000);
    let mt = soc.periph.timer.mtime(soc.cycle);
    assert!((1000..1010).contains(&mt), "mtime = {mt}");
    // msip: bit 0 R/W, mip.MSIP
    assert_eq!(load(&mut soc, 0x2000_0000), 0);
    store(&mut soc, 0x2000_0000, 0xFFFF_FFFF);
    assert_eq!(load(&mut soc, 0x2000_0000), 1);
    exec(&mut soc, csr(2, 5, 0x344, 0));
    assert_eq!(soc.regs[5] & 0x8, 0x8, "mip.MSIP");
    // ... which raises the software interrupt when enabled
    soc.csr.mie = 1 << 3;
    soc.csr.mstatus = 1 << 3;
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, Some(0x8000_0003));
    store(&mut soc, 0x2000_0000, 0);
    soc.csr.mstatus = 1 << 3;
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, None);
}

#[test]
fn real_has_no_clint() {
    let mut soc = new_soc(Profile::Real);
    soc.regs[1] = 0x2000_0000;
    soc.regs[2] = 1;
    exec(&mut soc, sw(2, 1, 0));
    exec(&mut soc, lw(5, 1, 0));
    assert_eq!(soc.regs[5], 0);
    soc.csr.mie = 1 << 3;
    soc.csr.mstatus = 1 << 3;
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, None, "no MSIP source");
    soc.regs[1] = 0x2000_BFF8;
    soc.cycle = 5000;
    exec(&mut soc, lw(5, 1, 0));
    assert_eq!(soc.regs[5], 0);
}

// ----- the liteuart view ----------------------------------------------------------------

#[test]
fn liteuart_registers() {
    let mut u = Uart::default();
    assert_eq!(u.lite_read(0x04, 10), 0, "TXFULL");
    assert_eq!(u.lite_read(0x08, 10), 1, "RXEMPTY");
    assert_eq!(u.lite_read(0x0C, 10), 1, "EV_STATUS: TX");
    assert_eq!(u.lite_read(0x10, 10), 1, "EV_PENDING: TX");
    assert_eq!(u.lite_read(0x14, 10), 0, "EV_ENABLE resets to 0");
    assert!(!u.lite_irq(10));
    // EV_ENABLE: 2 bits, from the edge on
    u.lite_write(0x14, 0xFF, 20);
    assert_eq!(u.lite_read(0x14, 20), 0);
    assert_eq!(u.lite_read(0x14, 21), 3);
    assert!(u.lite_irq(21), "TX enabled and the TX FIFO not full");
    u.lite_write(0x14, 2, 30);
    assert!(!u.lite_irq(31), "RX enabled, nothing received");
    // EV_PENDING ignores writes (levels)
    u.lite_write(0x10, 3, 40);
    assert_eq!(u.lite_read(0x10, 41), 1);
    // RX: a byte arrives (1486 cycles after its start bit), RXTX pops it
    u.host_send(b"Z", 100, false);
    assert!(!u.lite_irq(100 + 1485));
    assert!(u.lite_irq(100 + 1486));
    assert_eq!(u.lite_read(0x08, 2000), 0);
    assert_eq!(u.lite_read(0x10, 2000), 3);
    assert_eq!(u.lite_read(0x00, 2000), b'Z' as u32);
    assert_eq!(
        u.lite_read(0x08, 2001),
        1,
        "popped at the end of the read cycle"
    );
    assert!(!u.lite_irq(2001));
    // Mirrored every 256 bytes; other offsets read 0
    assert_eq!(u.lite_read(0x114, 2002), 2);
    assert_eq!(u.lite_read(0x18, 2002), 0);
    // TX: RXTX writes transmit; 40 writes fill the FIFO (TXFULL, no TX event)
    for k in 0..40u64 {
        u.lite_write(0x00, b'a' as u32 + k as u32, 3000 + k);
    }
    assert_eq!(u.lite_read(0x04, 3040), 1, "TXFULL");
    assert_eq!(u.lite_read(0x10, 3040) & 1, 0);
    u.advance(200_000);
    assert_eq!(&u.tx_bytes()[..3], b"abc");
    assert_eq!(
        u.tx_bytes().len(),
        33,
        "one in the transmitter, 32 in the FIFO"
    );
    assert_eq!(u.lite_read(0x04, 200_000), 0);
}

#[test]
fn liteuart_drives_meip_on_linux_only() {
    let mip = |soc: &mut Soc| {
        exec(soc, csr(2, 5, 0x344, 0));
        soc.regs[5] & (1 << 11)
    };
    // Real: MEIP while the RX FIFO holds data
    let mut real = new_soc(Profile::Real);
    real.uart_send(b"x");
    real.cycle += 2000;
    assert_ne!(mip(&mut real), 0);
    // Linux: EV_ENABLE & EV_PENDING
    let mut soc = new_soc(Profile::Linux);
    soc.uart_send(b"x");
    soc.cycle += 2000;
    assert_eq!(mip(&mut soc), 0, "EV_ENABLE = 0");
    soc.regs[1] = 0x4000_0000;
    soc.regs[2] = 2;
    exec(&mut soc, sw(2, 1, 0x14)); // EV_ENABLE = RX
    assert_ne!(mip(&mut soc), 0);
    exec(&mut soc, lw(5, 1, 0x08));
    assert_eq!(soc.regs[5], 0, "RXEMPTY");
    exec(&mut soc, lw(5, 1, 0x00));
    assert_eq!(soc.regs[5], b'x' as u32);
    assert_eq!(mip(&mut soc), 0, "RX drained");
    soc.regs[2] = 1;
    exec(&mut soc, sw(2, 1, 0x14)); // EV_ENABLE = TX
    assert_ne!(mip(&mut soc), 0, "the TX FIFO isn't full");
    // The interrupt is taken
    soc.csr.mie = 1 << 11;
    soc.csr.mstatus = 1 << 3;
    assert_eq!(exec(&mut soc, addi(0, 0, 0)).trap, Some(0x8000_000B));
    // RXTX transmits at the 115200-baud timing
    soc.regs[2] = b'K' as u32;
    exec(&mut soc, sw(2, 1, 0));
    soc.cycle += 3000;
    assert_eq!(soc.uart_received(), b"K");
}

// ----- load_linux -----------------------------------------------------------------------

#[test]
fn load_linux_sets_up_memory_and_registers() {
    let image: Vec<u8> = [addi(5, 0, 1), addi(6, 0, 2)]
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .collect();
    let dtb = [0xD0, 0x0D, 0xFE, 0xED, 1, 2];
    let mut soc = Soc::new(Profile::Linux);
    soc.regs[10] = 9;
    soc.regs[11] = 9;
    soc.load_linux(&image, &dtb).unwrap();
    assert_eq!(
        (soc.pc, soc.regs[10], soc.regs[11]),
        (0x8000_0000, 0, LINUX_DTB_ADDR)
    );
    assert!(soc.riscv_mode && !soc.csr.user);
    assert_eq!(soc.xmem_word(0x8000_0000), addi(5, 0, 1));
    assert_eq!(soc.xmem_word(LINUX_DTB_ADDR), 0xEDFE_0DD0);
    assert_eq!(soc.xmem_word(LINUX_DTB_ADDR + 4), 0x0201);
    let r = soc.step();
    assert_eq!(
        (r.cycles, soc.regs[5]),
        (2 + XMEM_FETCH_FILL, 1),
        "a cold fetch"
    );
    // What it refuses
    let room = (LINUX_DTB_ADDR - 0x8000_0000) as usize;
    let mut soc = Soc::new(Profile::Linux);
    assert!(
        soc.load_linux(&vec![0; room], &dtb).is_ok(),
        "up to the DTB"
    );
    assert!(
        soc.load_linux(&vec![0; room + 1], &dtb).is_err(),
        "overlaps the DTB"
    );
    assert!(soc.load_linux(&[], &dtb).is_err());
    assert!(soc.load_linux(&image, &vec![0; LINUX_DTB_MAX]).is_ok());
    assert!(soc.load_linux(&image, &vec![0; LINUX_DTB_MAX + 1]).is_err());
    assert!(soc.load_linux(&image, &[]).is_err());
    assert!(Soc::new(Profile::Real).load_linux(&image, &dtb).is_err());
    assert!(Soc::new(Profile::IsaTest).load_linux(&image, &dtb).is_err());
}

#[test]
fn linux_profile_switches() {
    let soc = Soc::new(Profile::Linux);
    assert!(soc.a_ext && soc.u_mode && soc.m_ext);
    for p in [Profile::Real, Profile::IsaTest] {
        let soc = Soc::new(p);
        assert!(soc.a_ext && soc.u_mode, "{p:?}");
    }
    assert_eq!(Profile::Linux.rom_bytes(), Profile::Real.rom_bytes());
    assert_eq!(Profile::Linux.xmem_bytes(), Profile::Real.xmem_bytes());
}
