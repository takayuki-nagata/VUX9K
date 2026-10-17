// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! CPU core: RV32I and Hack semantics, cycle costs, traps, CSRs and the real-profile
//! memory decode, each checked against what the RTL does (see src/soc.rs).

use vux9k_emu::{Isa, Profile, Soc};

// ----- RV32I encoders -------------------------------------------------------------
fn r(f7: u32, rs2: u32, rs1: u32, f3: u32, rd: u32) -> u32 {
    (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0x33
}
fn i(op: u32, imm: i32, rs1: u32, f3: u32, rd: u32) -> u32 {
    ((imm as u32 & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
}
fn addi(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x13, imm, rs1, 0, rd)
}
fn s(imm: i32, rs2: u32, rs1: u32, f3: u32) -> u32 {
    let u = imm as u32;
    ((u >> 5 & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((u & 0x1F) << 7) | 0x23
}
fn b(imm: i32, rs2: u32, rs1: u32, f3: u32) -> u32 {
    let u = imm as u32;
    ((u >> 12 & 1) << 31)
        | ((u >> 5 & 0x3F) << 25)
        | (rs2 << 20)
        | (rs1 << 15)
        | (f3 << 12)
        | ((u >> 1 & 0xF) << 8)
        | ((u >> 11 & 1) << 7)
        | 0x63
}
fn lui(rd: u32, imm20: u32) -> u32 {
    (imm20 << 12) | (rd << 7) | 0x37
}
fn jal(rd: u32, imm: i32) -> u32 {
    let u = imm as u32;
    ((u >> 20 & 1) << 31)
        | ((u >> 1 & 0x3FF) << 21)
        | ((u >> 11 & 1) << 20)
        | ((u >> 12 & 0xFF) << 12)
        | (rd << 7)
        | 0x6F
}
fn csr(f3: u32, rd: u32, csr: u32, rs1: u32) -> u32 {
    (csr << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0x73
}
const ECALL: u32 = 0x0000_0073;
const EBREAK: u32 = 0x0010_0073;
const MRET: u32 = 0x3020_0073;

fn load(profile: Profile, words: &[u32]) -> Soc {
    let mut soc = Soc::new(profile);
    let bytes: Vec<u8> = words.iter().flat_map(|w| w.to_le_bytes()).collect();
    soc.load_iram(0, &bytes);
    soc
}

fn run_n(soc: &mut Soc, n: usize) {
    for _ in 0..n {
        soc.step();
    }
}

#[test]
fn alu_and_immediates() {
    let mut soc = load(
        Profile::Real,
        &[
            addi(1, 0, -5),
            addi(2, 0, 7),
            r(0, 2, 1, 0, 3),                   // add x3 = 2
            r(0x20, 2, 1, 0, 4),                // sub x4 = -12
            r(0, 2, 1, 2, 5),                   // slt x5 = 1
            r(0, 2, 1, 3, 6),                   // sltu x6 = 0
            i(0x13, 4, 1, 5, 7) | (0x20 << 25), // srai x7 = -5 >> 4 = -1
            i(0x13, 4, 1, 5, 8),                // srli x8
            lui(9, 0xABCDE),
            0x0000_0517, // auipc x10, 0
        ],
    );
    run_n(&mut soc, 10);
    assert_eq!(soc.regs[3], 2);
    assert_eq!(soc.regs[4] as i32, -12);
    assert_eq!(soc.regs[5], 1);
    assert_eq!(soc.regs[6], 0);
    assert_eq!(soc.regs[7] as i32, -1);
    assert_eq!(soc.regs[8], 0xFFFF_FFFB >> 4);
    assert_eq!(soc.regs[9], 0xABCD_E000);
    assert_eq!(soc.regs[10], 9 * 4);
    assert_eq!(soc.cycle, 20, "ALU instructions take 2 cycles each");
}

#[test]
fn loads_stores_sign_extension_and_cycles() {
    // D-RAM at 0x2000_0000: lui x1, 0x20000
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x20000),
            addi(2, 0, -2),       // 0xFFFF_FFFE
            s(4, 2, 1, 0),        // sb -> byte 4
            i(0x03, 4, 1, 0, 3),  // lb  x3 = -2
            i(0x03, 4, 1, 4, 4),  // lbu x4 = 0xFE
            s(8, 2, 1, 1),        // sh
            i(0x03, 8, 1, 1, 5),  // lh = -2
            i(0x03, 8, 1, 5, 6),  // lhu = 0xFFFE
            s(12, 2, 1, 2),       // sw
            i(0x03, 12, 1, 2, 7), // lw
        ],
    );
    run_n(&mut soc, 10);
    assert_eq!(soc.regs[3] as i32, -2);
    assert_eq!(soc.regs[4], 0xFE);
    assert_eq!(soc.regs[5] as i32, -2);
    assert_eq!(soc.regs[6], 0xFFFE);
    assert_eq!(soc.regs[7], 0xFFFF_FFFE);
    // 2 ALU (2 cycles) + 8 memory ops (3 cycles)
    assert_eq!(soc.cycle, 2 * 2 + 8 * 3);
}

#[test]
fn branches_and_jal() {
    let mut soc = load(
        Profile::Real,
        &[
            addi(1, 0, 1),
            b(8, 0, 1, 1), // bne x1, x0, +8 (taken)
            addi(2, 0, 99),
            jal(3, 8), // at 12: jal x3, +8 -> 20
            addi(2, 0, 98),
            addi(4, 0, 4), // at 20
        ],
    );
    run_n(&mut soc, 4);
    assert_eq!(soc.regs[2], 0, "the taken branch skips addi");
    assert_eq!(soc.regs[3], 16, "jal links pc+4");
    assert_eq!(soc.regs[4], 4);
    assert_eq!(soc.pc, 24);
}

#[test]
fn trap_priority_and_mtval() {
    // mtvec = 0x100 (a j . there)
    let handler = 0x100u32;
    let program = |body: &[u32]| {
        let mut w = vec![addi(1, 0, handler as i32), csr(1, 0, 0x305, 1)];
        w.extend_from_slice(body);
        w.resize((handler / 4) as usize, 0x13);
        w.push(jal(0, 0));
        w
    };
    // illegal: all-zero word, mtval = the word
    let mut soc = load(Profile::Real, &program(&[0]));
    run_n(&mut soc, 3);
    assert_eq!(
        (soc.pc, soc.csr.mcause, soc.csr.mtval, soc.csr.mepc),
        (handler, 2, 0, 8)
    );
    // ecall (11), ebreak (3, mtval = pc)
    let mut soc = load(Profile::Real, &program(&[ECALL]));
    run_n(&mut soc, 3);
    assert_eq!((soc.csr.mcause, soc.csr.mtval), (11, 0));
    let mut soc = load(Profile::Real, &program(&[EBREAK]));
    run_n(&mut soc, 3);
    assert_eq!((soc.csr.mcause, soc.csr.mtval), (3, 8));
    // misaligned lw (4) and sw (6): mtval = address, no side effects
    let mut soc = load(
        Profile::Real,
        &program(&[lui(5, 0x20000), i(0x03, 2, 5, 2, 6)]),
    );
    run_n(&mut soc, 4);
    assert_eq!(
        (soc.csr.mcause, soc.csr.mtval, soc.regs[6]),
        (4, 0x2000_0002, 0)
    );
    let mut soc = load(Profile::Real, &program(&[lui(5, 0x20000), s(1, 5, 5, 2)]));
    run_n(&mut soc, 4);
    assert_eq!((soc.csr.mcause, soc.csr.mtval), (6, 0x2000_0001));
    // misaligned jump target (cause 0, mtval = target), rd not written
    let mut soc = load(Profile::Real, &program(&[jal(7, 6)]));
    run_n(&mut soc, 3);
    assert_eq!((soc.csr.mcause, soc.csr.mtval, soc.regs[7]), (0, 8 + 6, 0));
    // a CSR the CPU doesn't implement (mcounteren: no U-mode) is illegal
    let mut soc = load(Profile::Real, &program(&[csr(2, 5, 0x306, 0)]));
    run_n(&mut soc, 3);
    assert_eq!(soc.csr.mcause, 2);
    // writing a read-only CSR (unimp = csrrw x0, cycle, x0) is illegal
    let mut soc = load(Profile::Real, &program(&[0xC000_1073]));
    run_n(&mut soc, 3);
    assert_eq!(soc.csr.mcause, 2);
}

#[test]
fn mret_restores_mie() {
    let mut soc = load(
        Profile::Real,
        &[
            addi(1, 0, 16),
            csr(1, 0, 0x341, 1), // mepc = 16
            addi(1, 0, 0x80),
            csr(2, 0, 0x300, 1), // mstatus |= MPIE
            MRET,                // at 16? no: at 16 is this word -> loops; jump lands on it
        ],
    );
    run_n(&mut soc, 5);
    assert_eq!(soc.pc, 16);
    assert_eq!(soc.csr.mstatus & 0x88, 0x88, "MIE <= MPIE, MPIE <= 1");
}

#[test]
fn counters_and_write_replacing_increment() {
    let mut soc = load(
        Profile::Real,
        &[
            csr(2, 1, 0xC02, 0), // csrr x1, instret
            addi(0, 0, 0),
            addi(0, 0, 0),
            csr(2, 2, 0xC02, 0), // csrr x2, instret -> x1 + 3
            csr(2, 3, 0xC00, 0), // csrr x3, cycle
            csr(1, 0, 0xB02, 0), // csrw minstret, x0
            csr(2, 4, 0xB02, 0), // csrr x4, minstret -> 0 (the write replaced its own count)
        ],
    );
    run_n(&mut soc, 7);
    assert_eq!(soc.regs[2] - soc.regs[1], 3);
    assert_eq!(
        soc.regs[3],
        4 * 2 + 1,
        "cycle as read in EXECUTE of the 5th instruction"
    );
    assert_eq!(soc.regs[4], 0);
}

#[test]
fn real_profile_aliasing_and_iram_stores() {
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x20002), // 0x2000_2000 = D-RAM word 0 (8 KB alias)
            addi(2, 0, 0x5A),
            s(0, 2, 1, 0), // sb 0x5A
            lui(3, 0x20000),
            i(0x03, 0, 3, 4, 4), // lbu x4 from 0x2000_0000 -> 0x5A
            lui(5, 0x1),         // 0x1000: I-RAM via data port
            s(0, 2, 5, 0),       // sb 0x5A into I-RAM: whole word 0x5A5A5A5A
            lui(6, 0x5),         // 0x5000 = I-RAM 0x1000 (16 KB alias)
            i(0x03, 0, 6, 2, 7), // lw x7
        ],
    );
    run_n(&mut soc, 9);
    assert_eq!(soc.regs[4], 0x5A);
    assert_eq!(
        soc.regs[7], 0x5A5A_5A5A,
        "I-RAM has no byte enables: SB writes the replicated word"
    );
}

// ----- Hack ----------------------------------------------------------------------
// Hack runs from the PSRAM: instructions in its I segment (HACK_I_BASE), data in its D
// segment (HACK_D_BASE), both through the window's caches.
fn c(dest: u16, comp: u16, a: bool, jump: u16) -> u16 {
    0xE000 | (if a { 0x1000 } else { 0 }) | (comp << 6) | (dest << 3) | jump
}

/// Hack instructions as an `hcc -r` image: big-endian 16-bit words.
fn hack_image(instrs: &[u16]) -> Vec<u8> {
    instrs.iter().flat_map(|i| i.to_be_bytes()).collect()
}

/// A Real SoC started in Hack on `instrs` (load_app: the I segment, Hack from 0).
fn hack_app(instrs: &[u16]) -> Soc {
    let mut soc = Soc::new(Profile::Real);
    soc.load_app(&hack_image(instrs), Isa::Hack).unwrap();
    soc
}

/// The PSRAM address of Hack data word `a`.
fn dseg(a: u32) -> u32 {
    vux9k_emu::soc::HACK_D_BASE + 4 * a
}

#[test]
fn hack_semantics_and_cycles() {
    use vux9k_emu::soc::{XMEM_READ_LATENCY as R, XMEM_WRITE_LATENCY as W};
    // @7, D=A, @100, M=D, @100, D=M+1 (reads M: 4 cycles), AD=D+1 (dual dest: 3 cycles).
    // The first instruction of each word misses the instruction cache (R), M=D stores to
    // the PSRAM (W) and allocates the line D=M+1 then hits.
    let mut soc = hack_app(&[
        7,
        c(0b010, 0b110000, false, 0),
        100,
        c(0b001, 0b001100, false, 0),
        100,
        c(0b010, 0b110111, true, 0),
        c(0b110, 0b011111, false, 0),
    ]);
    run_n(&mut soc, 7);
    assert!(!soc.riscv_mode);
    assert_eq!(soc.regs[2], 9, "D");
    assert_eq!(soc.regs[1], 9, "A");
    assert_eq!(soc.xmem_word(dseg(100)), 7);
    assert_eq!(
        soc.cycle,
        (2 + R + 2 + 2 + R + 2 + W + 2 + R + 4 + 3 + R) as u64
    );
}

#[test]
fn hack_window_waits() {
    use vux9k_emu::soc::{XMEM_READ_LATENCY as R, XMEM_WRITE_LATENCY as W};
    let mut soc = hack_app(&[
        3,
        c(0b111, 0b110111, true, 0), // AMD=M+1: M[3] misses (R), the store (W), D last
        c(0b010, 0b110000, true, 0), // D=M: a new word, a miss, the fetch too
        3,
        c(0b001, 0b110111, true, 0), // M=M+1: M[3] hits (the store allocated it), the store
        c(0b001, 0b111010, false, 0), // M=-1: the store in EXECUTE
    ]);
    let mut cycles = vec![];
    let mut stores = vec![];
    for _ in 0..6 {
        let r = soc.step();
        cycles.push(r.cycles);
        stores.push(r.store);
    }
    assert_eq!(
        cycles,
        [2 + R, 4 + R + W + 1, 2 + R + 2 + R, 2, R + 4 + W, 2 + W],
        "FETCH (+R on a miss), EXECUTE (+W for M=), MEM_WAIT (+R on a miss), HACK_WB (+W), HACK_WR_D"
    );
    // The bus shows the CPU's A; the word is in the D segment
    assert_eq!(stores[1], Some((3, 1, 0b1111)));
    assert_eq!(stores[4], Some((3, 2, 0b1111)));
    assert_eq!((soc.regs[1], soc.regs[2]), (3, 0));
    assert_eq!(soc.xmem_word(dseg(3)), 0xFFFF_FFFF);
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses, st.stores), (1, 2, 3));
}

#[test]
fn hack_registers_are_32_bit_and_conditions_16_bit() {
    // @0x7FFF, D=A, D=D+1 -> 0x8000 (negative as 16-bit), @4, D;JLT -> jumps to 8 (A*2)
    let mut soc = hack_app(&[
        0x7FFF,
        c(0b010, 0b110000, false, 0),
        c(0b010, 0b011111, false, 0),
        4,
        c(0, 0b001100, false, 4),
    ]);
    run_n(&mut soc, 5);
    assert_eq!(soc.regs[2], 0x8000);
    assert_eq!(soc.pc, 8, "JLT on bit 15, target A << 1");
}

#[test]
fn hack_data_has_no_alias() {
    // @5, D=A, @0x0805, M=D, @5, D=M: before the D segment (D-RAM), 0x0805 was word 5
    let mut soc = hack_app(&[
        5,
        c(0b010, 0b110000, false, 0),
        0x0805,
        c(0b001, 0b001100, false, 0),
        5,
        c(0b010, 0b110000, true, 0),
    ]);
    run_n(&mut soc, 6);
    assert_eq!(soc.regs[2], 0, "word 5 is still 0");
    assert_eq!(soc.xmem_word(dseg(0x0805)), 5);
    assert_eq!(soc.dram_word_index(5), 0, "D-RAM untouched");
}

#[test]
fn power_on_starts_rv32() {
    // No ISA guess: a Hack-looking first word (@7) runs as RV32, and traps as illegal
    let mut soc = load(Profile::Real, &[0x0007_0007]);
    soc.step();
    assert!(soc.riscv_mode);
    assert_eq!(soc.csr.mcause, 2);
}

// ----- application images (load_app, vux9k-emu --load --mode) -------------------------
#[test]
fn load_app_starts_hack_in_hack_mode() {
    use vux9k_emu::soc::HACK_I_BASE;
    // @19, D=A, @100, M=D (@19, 0x0013, is RV32's OP-IMM: the mode, not the code, decides)
    let prog = [
        19,
        c(0b010, 0b110000, false, 0),
        100,
        c(0b001, 0b001100, false, 0),
    ];

    let mut soc = Soc::new(Profile::Real);
    soc.load_app(&hack_image(&prog), Isa::Hack).unwrap();
    assert_eq!(
        soc.xmem_word(HACK_I_BASE),
        (c(0b010, 0b110000, false, 0) as u32) << 16 | 19
    );
    assert_eq!(soc.iram_word(0), 0, "I-RAM untouched");
    run_n(&mut soc, 4);
    assert!(!soc.riscv_mode);
    assert_eq!(soc.xmem_word(dseg(100)), 19);

    // At an offset in the segment: the CPU still starts at 0, as after a soft reset
    let mut soc = Soc::new(Profile::Real);
    soc.load_app_at(&hack_image(&prog), Isa::Hack, 0x100)
        .unwrap();
    assert_eq!((soc.pc, soc.riscv_mode), (0, false));
    assert_eq!(
        soc.xmem_word(HACK_I_BASE + 0x104),
        100 | (c(0b001, 0b001100, false, 0) as u32) << 16
    );
}

#[test]
fn load_app_starts_rv32_in_rv32_mode() {
    let prog: Vec<u8> = [addi(1, 0, 5), addi(1, 1, 1)]
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .collect();
    let mut soc = Soc::new(Profile::Real);
    soc.load_app(&prog, Isa::Rv32).unwrap();
    run_n(&mut soc, 2);
    assert!(soc.riscv_mode);
    assert_eq!(soc.regs[1], 6);
}

#[test]
fn load_app_rejects_what_the_board_cannot_load() {
    let mut soc = Soc::new(Profile::Real);
    assert!(soc
        .load_app(&[0x40, 0x00, 0x13], Isa::Hack)
        .unwrap_err()
        .contains("even length"));
    assert!(soc.load_app(&[], Isa::Rv32).is_err());
    assert!(soc.load_app(&vec![0x13; 14 * 1024], Isa::Rv32).is_ok());
    assert!(soc
        .load_app(&vec![0x13; 14 * 1024 + 4], Isa::Rv32)
        .unwrap_err()
        .contains("fits on the board"));
    // Hack: its 64 KB segment, at a word
    assert!(soc.load_app(&vec![0; 64 * 1024], Isa::Hack).is_ok());
    for (len, at) in [(64 * 1024 + 2, 0), (64 * 1024 - 4, 8), (2, 2), (0, 0)] {
        let e = soc.load_app_at(&vec![0; len], Isa::Hack, at).unwrap_err();
        assert!(e.contains("Hack instruction segment"), "{e}");
    }
    assert!(Soc::new(Profile::IsaTest)
        .load_app(&[0, 7], Isa::Hack)
        .unwrap_err()
        .contains("PSRAM"));
}

#[test]
fn load_app_at_the_psram_runs_rv32_in_place() {
    use vux9k_emu::soc::{XMEM_BASE, XMEM_READ_LATENCY};
    // Bigger than the I-RAM window: the 14 KB limit is I-RAM's only
    let mut prog: Vec<u8> = [addi(1, 0, 5), addi(1, 1, 1)]
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .collect();
    prog.resize(16 * 1024, 0);
    let at = XMEM_BASE + 0x100;
    let mut soc = Soc::new(Profile::Real);
    soc.load_app_at(&prog, Isa::Rv32, at).unwrap();
    assert_eq!((soc.pc, soc.xmem_word(at + 4)), (at, addi(1, 1, 1)));
    assert_eq!(soc.iram_word(0), 0, "I-RAM untouched");
    run_n(&mut soc, 2);
    assert!(soc.riscv_mode);
    assert_eq!(soc.regs[1], 6);
    assert_eq!(soc.cycle, 2 * (2 + XMEM_READ_LATENCY) as u64);
    // At 0 it is load_app, with its limit
    let mut soc = Soc::new(Profile::Real);
    assert!(soc.load_app_at(&prog, Isa::Rv32, 0).is_err());
    soc.load_app_at(&prog[..8], Isa::Rv32, 0).unwrap();
    assert_eq!((soc.pc, soc.iram_word(0)), (0, addi(1, 0, 5)));
}

#[test]
fn load_app_at_rejects_what_the_board_cannot_start() {
    use vux9k_emu::soc::XMEM_BASE;
    let mut soc = Soc::new(Profile::Real);
    let nop = 0x13u32.to_le_bytes();
    for (isa, addr) in [
        (Isa::Rv32, 0x100),
        (Isa::Rv32, 0x2000_0000),
        (Isa::Rv32, 0x9000_0000),
    ] {
        let e = soc.load_app_at(&nop, isa, addr).unwrap_err();
        assert!(e.contains("into the PSRAM"), "{e}");
    }
    // Hack's load address is an offset in its segment
    let e = soc.load_app_at(&nop, Isa::Hack, XMEM_BASE).unwrap_err();
    assert!(e.contains("Hack instruction segment"), "{e}");
    assert!(soc.load_app_at(&[], Isa::Rv32, XMEM_BASE).is_err());
    let e = soc.load_app_at(&nop, Isa::Rv32, XMEM_BASE + 2).unwrap_err();
    assert!(e.contains("word-aligned"), "{e}");
    // Up to the window's last word, not past it, nor into its 8 MB repeat
    let end = XMEM_BASE + 8 * 1024 * 1024;
    assert!(soc.load_xmem(end - 4, &nop).is_ok());
    assert!(soc.load_xmem(end - 4, &[0; 5]).is_err());
    assert!(soc.load_xmem(end, &nop).is_err());
    // A partial last word is padded with zeros
    soc.load_xmem(XMEM_BASE, &[1, 2, 3, 4, 5]).unwrap();
    assert_eq!(soc.xmem_word(XMEM_BASE + 4), 5);
    // The riscv-tests profile has no window
    let mut soc = Soc::new(Profile::IsaTest);
    assert!(soc.load_xmem(XMEM_BASE, &nop).is_err());
}

#[test]
fn hpm_csrs_exist_in_their_ranges() {
    use vux9k_emu::csr::exists;
    // mcountinhibit, mhpmevent3-31, mhpmcounter3-31 and their high halves read 0
    for addr in [0x320, 0x323, 0x33F, 0xB03, 0xB1F, 0xB83, 0xB9F] {
        assert!(exists(addr), "{addr:#x}");
    }
    // their neighbors don't exist (0x321/0x322 reserved; no hpmcounter without Zihpm)
    for addr in [
        0x321, 0x322, 0xB01, 0xB20, 0xB81, 0xBA0, 0xC03, 0xC83, 0x306,
    ] {
        assert!(!exists(addr), "{addr:#x}");
    }
}

// ----- the wait-state window (soc_xmem_bridge + the 8 MB PSRAM, RV32 region 8) --------
use vux9k_emu::soc::{XMEM_READ_LATENCY, XMEM_WRITE_LATENCY};

/// lui + addi loading `v` into `rd`.
fn li(rd: u32, v: u32) -> [u32; 2] {
    let lo = ((v << 20) as i32) >> 20;
    [lui(rd, v.wrapping_sub(lo as u32) >> 12), addi(rd, rd, lo)]
}
const FENCE_I: u32 = 0x0000_100F;

#[test]
fn xmem_loads_stores_aliases_and_wait_cycles() {
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000), // 0x8000_0000
            addi(2, 0, -2),
            s(0x10, 2, 1, 2), // sw -2
            addi(3, 0, 0x5A),
            s(0x11, 3, 1, 0), // sb 0x5A into byte 1: the window has byte enables
            lui(4, 0x80800),  // 0x8080_0000: the 8 MB repeat
            i(0x03, 0x10, 4, 2, 5), // lw x5 = 0xFFFF_5AFE
            i(0x03, 0x11, 1, 4, 6), // lbu x6 = 0x5A
            i(0x03, 0x12, 1, 1, 7), // lh x7 = -1
        ],
    );
    run_n(&mut soc, 9);
    assert_eq!(soc.regs[5], 0xFFFF_5AFE);
    assert_eq!(soc.regs[6], 0x5A);
    assert_eq!(soc.regs[7], 0xFFFF_FFFF);
    assert_eq!(
        soc.xmem_word(0x8F80_0010),
        0xFFFF_5AFE,
        "0x8F80_0010 is 0x8000_0010, 248 MB up"
    );
    assert_eq!(soc.xmem_word(0x8000_0810), 0, "no 2 KB repeat any more");
    let stores = 2 * (3 + XMEM_WRITE_LATENCY) as u64;
    let loads = 3 * 3; // data-cache hits: the sw allocated the line, the sb merged into it
    assert_eq!(soc.cycle, 4 * 2 + stores + loads);
    // Region 9 is unmapped: reads 0, writes ignored, no wait
    let mut soc = load(
        Profile::Real,
        &[lui(1, 0x90000), s(0, 1, 1, 2), i(0x03, 0, 1, 2, 2)],
    );
    run_n(&mut soc, 3);
    assert_eq!((soc.regs[2], soc.cycle), (0, 2 + 3 + 3));
    // The riscv-tests profile has no window: no waits there either
    let mut soc = load(
        Profile::IsaTest,
        &[lui(1, 0x80000), s(0, 1, 1, 2), i(0x03, 0, 1, 2, 2)],
    );
    run_n(&mut soc, 3);
    assert_eq!(soc.cycle, 2 + 3 + 3);
}

/// I-RAM code that copies `routine` to 0x8000_0000, sets x9 to the return address and
/// jumps there; returns the program and the number of steps up to the jump.
fn xmem_call(routine: &[u32]) -> (Vec<u32>, usize) {
    let mut w = vec![lui(1, 0x80000)];
    for (k, word) in routine.iter().enumerate() {
        w.extend(li(2, *word));
        w.push(s(4 * k as i32, 2, 1, 2));
    }
    w.push(FENCE_I);
    let ret = 4 * (w.len() as u32 + 2);
    w.push(addi(9, 0, ret as i32));
    w.push(i(0x67, 0, 1, 0, 0)); // jalr x0, 0(x1)
    let steps = w.len();
    w.push(jal(0, 0)); // ret: spin
    (w, steps)
}

#[test]
fn xmem_fetch_waits_and_runs_late() {
    // csrr x8, mcycle; csrr x10, time; jalr x0, 0(x9)
    let (prog, steps) = xmem_call(&[
        csr(2, 8, 0xB00, 0),
        csr(2, 10, 0xC01, 0),
        i(0x67, 0, 9, 0, 0),
    ]);
    let mut soc = load(Profile::Real, &prog);
    run_n(&mut soc, steps);
    assert_eq!(soc.pc, 0x8000_0000);
    let (c0, mc0) = (soc.cycle, soc.csr.mcycle);
    let r = soc.step();
    let w = XMEM_READ_LATENCY as u64;
    assert_eq!(
        (r.cycle, r.cycles as u64),
        (c0, 2 + w),
        "Retire::cycle is the first FETCH cycle"
    );
    assert_eq!(
        soc.regs[8] as u64,
        mc0 + w + 1,
        "mcycle as read in EXECUTE, after the wait"
    );
    let r = soc.step();
    assert_eq!(
        soc.regs[10] as u64,
        r.cycle + w + 1,
        "mtime as read in EXECUTE"
    );
    soc.step();
    assert_eq!(soc.pc, 4 * steps as u32, "returned to I-RAM");
    assert_eq!(soc.cycle, c0 + 3 * (2 + w));
}

#[test]
fn xmem_fetch_samples_the_interrupt_in_its_last_cycle() {
    // An interrupt pending from the last FETCH cycle on is taken; one from the cycle
    // after it is not (the next instruction takes it)
    for (late, trap) in [(0, true), (1, false)] {
        let (mut prog, steps) = xmem_call(&[addi(11, 0, 1), i(0x67, 0, 9, 0, 0)]);
        prog.resize(0x40, 0x13);
        prog.push(jal(0, 0)); // handler at 0x100: spin
        let mut soc = load(Profile::Real, &prog);
        run_n(&mut soc, steps);
        soc.csr.mtvec = 0x100;
        soc.csr.mie = 1 << 7;
        soc.csr.mstatus = 1 << 3;
        let c0 = soc.cycle;
        let w = XMEM_READ_LATENCY as u64;
        // timer_irq in cycle c is mtime(c-1) >= mtimecmp, and mtime(c) == c here
        let cmp = c0 + w - 1 + late;
        soc.periph.timer.write(0xC, (cmp >> 32) as u32, c0 - 3);
        soc.periph.timer.write(0x8, cmp as u32, c0 - 2);
        let r = soc.step();
        assert_eq!(
            r.trap.is_some(),
            trap,
            "mtimecmp at the last FETCH cycle + {late}"
        );
        assert_eq!(r.cycles as u64, 2 + w);
        assert_eq!(soc.regs[11], if trap { 0 } else { 1 });
    }
}

#[test]
fn xmem_fetch_hits_the_icache_and_stores_invalidate_it() {
    // A two-instruction routine at 0x8000_0000 (addi x11 += 1; jalr x0, 0(x9)) called
    // four times from I-RAM: cold, warm, after a store to 0x8000_1000 (another word on
    // the routine's first line), and through the 8 MB alias
    let mut w = vec![lui(1, 0x80000)];
    for (k, word) in [addi(11, 11, 1), i(0x67, 0, 9, 0, 0)].iter().enumerate() {
        w.extend(li(2, *word));
        w.push(s(4 * k as i32, 2, 1, 2));
    }
    let call = |w: &mut Vec<u32>, base: u32| {
        let ret = 4 * (w.len() as u32 + 2);
        w.push(addi(9, 0, ret as i32));
        w.push(i(0x67, 0, base, 0, 0));
    };
    call(&mut w, 1);
    call(&mut w, 1);
    w.push(lui(4, 0x80001));
    w.push(s(0, 0, 4, 2)); // sw x0, 0(x4)
    call(&mut w, 1);
    w.push(lui(5, 0x80800));
    call(&mut w, 5);
    w.push(jal(0, 0));
    let mut soc = load(Profile::Real, &w);
    let mut waits = vec![];
    while soc.pc != 4 * (w.len() as u32 - 1) {
        let r = soc.step();
        if r.pc >> 28 == 8 {
            waits.push(r.cycles - 2);
        }
    }
    let m = XMEM_READ_LATENCY;
    assert_eq!(waits, [m, m, 0, 0, m, 0, 0, 0]);
    assert_eq!(soc.regs[11], 4);
    let st = soc.caches.stats;
    assert_eq!((st.fetch_hits, st.fetch_misses), (5, 3));
    // The power-on reset empties it
    soc.reset();
    soc.pc = 0x8000_0000;
    soc.regs[9] = 0x8000_0004;
    assert_eq!(soc.step().cycles, 2 + m);
}

#[test]
fn xmem_loads_hit_the_dcache_by_its_rules() {
    // lw: cold, warm; another tag on the line (4 KB up) evicts it; a partial store hit
    // keeps it; a partial store miss invalidates the line whatever it holds; a full-word
    // store miss allocates it; the alias 8 MB up is the same line
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000),
            lui(2, 0x80001),
            i(0x03, 0x40, 1, 2, 5), // lw x5, 0x8000_0040: miss
            i(0x03, 0x40, 1, 2, 5), // hit
            i(0x03, 0x40, 2, 2, 6), // lw x6, 0x8000_1040: miss, evicts 0x40
            i(0x03, 0x40, 1, 2, 5), // miss
            s(0x40, 0, 1, 0),       // sb x0, 0x8000_0040: hit, merged
            i(0x03, 0x40, 1, 2, 5), // hit
            s(0x40, 0, 2, 0),       // sb x0, 0x8000_1040: partial miss, the line goes
            i(0x03, 0x40, 1, 2, 5), // miss
            s(0x40, 0, 2, 2),       // sw x0, 0x8000_1040: full-word miss, allocates
            i(0x03, 0x40, 2, 2, 6), // hit
            lui(4, 0x80801),
            i(0x03, 0x40, 4, 2, 6), // lw through the alias: hit
        ],
    );
    let m = XMEM_READ_LATENCY;
    let mut waits = vec![];
    for _ in 0..14 {
        let r = soc.step();
        if r.instr & 0x7F == 0x03 {
            waits.push(r.cycles - 3);
        }
    }
    assert_eq!(waits, [m, 0, m, m, 0, m, 0, 0]);
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses, st.stores), (4, 4, 3));
}
