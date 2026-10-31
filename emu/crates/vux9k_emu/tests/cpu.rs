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
    soc.load_rom(0, &bytes);
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
    // The riscv-tests' flat RAM at 0x1000 (the real profile has no RAM without waits)
    let mut soc = load(
        Profile::IsaTest,
        &[
            lui(1, 0x1),
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
fn real_profile_rom_aliases_and_ignored_stores() {
    let mut soc = load(
        Profile::Real,
        &[
            addi(2, 0, 0x5A),
            lui(5, 0x1),         // 0x1000: the ROM through the data port
            s(0, 2, 5, 2),       // sw: ignored
            s(1, 2, 5, 0),       // sb: ignored
            lui(6, 0x01235),     // 0x0123_5000 = the ROM's 0x1000 (16 KB aliases)
            i(0x03, 0, 6, 2, 7), // lw x7
            lui(1, 0x20000),     // 0x2000_0000: region 2, unassigned (D-RAM until it went)
            s(0, 2, 1, 2),       // sw: ignored
            i(0x03, 0, 1, 2, 4), // lw x4 = 0
            lui(3, 0xF0000),     // region 0xF, unassigned
            s(4, 2, 3, 2),
            i(0x03, 4, 3, 2, 8), // lw x8 = 0
        ],
    );
    soc.load_rom_words(0x400, &[0xCAFE_F00D]);
    run_n(&mut soc, 12);
    assert_eq!(
        soc.regs[7], 0xCAFE_F00D,
        "the ROM word, unchanged by the stores"
    );
    assert_eq!(soc.rom_word(0x1000), 0xCAFE_F00D);
    assert_eq!(
        (soc.regs[4], soc.regs[8]),
        (0, 0),
        "unassigned regions read 0"
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
    use vux9k_emu::soc::{XMEM_FETCH_FILL as R, XMEM_LINE_FILL as F};
    // @7, D=A, @100, M=D, @100, D=M+1 (reads M: 4 cycles), AD=D+1 (dual dest: 3 cycles).
    // The first instruction fills the instruction line all of them are on (R), M=D misses
    // the data cache (F: write-allocate, the line fills) and D=M+1 then hits.
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
    assert_eq!(soc.cycle, (2 + R + 2 + 2 + 2 + F + 2 + 4 + 3) as u64);
}

#[test]
fn hack_window_waits() {
    use vux9k_emu::soc::{XMEM_FETCH_FILL as R, XMEM_LINE_FILL as F};
    // Data word 3's PSRAM word is on instruction line 0, the program's: each store to it
    // invalidates the program's line, so the next fetch misses (R) whatever it is
    let mut soc = hack_app(&[
        3,
        c(0b111, 0b110111, true, 0), // AMD=M+1: M[3] misses (F), the store hits, D last
        c(0b010, 0b110000, true, 0), // D=M: M[1], the same line, a hit
        3,
        c(0b001, 0b110111, true, 0),  // M=M+1: M[3] hits, the store too
        c(0b001, 0b111010, false, 0), // M=-1: the store in EXECUTE, a hit
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
        [2 + R, 4 + F + 1, 2 + R + 2, 2, 4, 2 + R],
        "FETCH (+R on a miss), EXECUTE, MEM_WAIT (+F on a miss), HACK_WB, HACK_WR_D"
    );
    // The bus shows the CPU's A; the word is in the D segment
    assert_eq!(stores[1], Some((3, 1, 0b1111)));
    assert_eq!(stores[4], Some((3, 2, 0b1111)));
    assert_eq!((soc.regs[1], soc.regs[2]), (3, 0));
    assert_eq!(soc.xmem_word(dseg(3)), 0xFFFF_FFFF);
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses, st.stores), (2, 1, 3));
    assert_eq!((st.store_hits, st.store_misses, st.writebacks), (3, 0, 0));
}

#[test]
fn hack_am_m_plus_1_evicts_a_dirty_line() {
    use vux9k_emu::soc::{XMEM_FETCH_FILL as R, XMEM_LINE_DIRTY as DM, XMEM_LINE_FILL as F};
    // M[3] and M[515] share a data-cache line (512 words apart). M=1 in EXECUTE misses
    // (F) and dirties the line, and invalidates the program's instruction line (R again);
    // AM=M+1 at 515 evicts it (DM), its store then hits; D=M at 3 evicts 515's line, dirty
    // from that store, and reads the 1 back
    let mut soc = hack_app(&[
        3,
        c(0b001, 0b111111, false, 0), // M=1
        515,
        c(0b101, 0b110111, true, 0), // AM=M+1
        3,
        c(0b010, 0b110000, true, 0), // D=M
    ]);
    let cycles: Vec<u32> = (0..6).map(|_| soc.step().cycles).collect();
    assert_eq!(cycles, [2 + R, 2 + F, 2 + R, 4 + DM, 2, 4 + DM]);
    assert_eq!((soc.regs[1], soc.regs[2]), (3, 1));
    assert_eq!(soc.xmem_word(dseg(515)), 1);
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses), (0, 2));
    assert_eq!((st.store_hits, st.store_misses, st.writebacks), (1, 1, 2));
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
    // @5, D=A, @0x0805, M=D, @5, D=M: before the D segment (D-RAM's 2K words), 0x0805 was word 5
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
    assert_eq!(soc.rom_word(0), 0, "ROM untouched");
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
    // RV32 at 0: the 16 KB boot ROM
    assert!(soc.load_app(&vec![0x13; 16 * 1024], Isa::Rv32).is_ok());
    assert!(soc
        .load_app(&vec![0x13; 16 * 1024 + 4], Isa::Rv32)
        .unwrap_err()
        .contains("fits in the boot ROM"));
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
    use vux9k_emu::soc::{XMEM_BASE, XMEM_FETCH_FILL};
    // Bigger than the ROM: the 16 KB limit is the ROM's only
    let mut prog: Vec<u8> = [addi(1, 0, 5), addi(1, 1, 1)]
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .collect();
    prog.resize(16 * 1024 + 4, 0);
    let at = XMEM_BASE + 0x100;
    let mut soc = Soc::new(Profile::Real);
    soc.load_app_at(&prog, Isa::Rv32, at).unwrap();
    assert_eq!((soc.pc, soc.xmem_word(at + 4)), (at, addi(1, 1, 1)));
    assert_eq!(soc.rom_word(0), 0, "ROM untouched");
    run_n(&mut soc, 2);
    assert!(soc.riscv_mode);
    assert_eq!(soc.regs[1], 6);
    assert_eq!(
        soc.cycle,
        (2 + XMEM_FETCH_FILL + 2) as u64,
        "one line: a fill, then a hit"
    );
    // At 0 it is load_app, with its limit
    let mut soc = Soc::new(Profile::Real);
    assert!(soc.load_app_at(&prog, Isa::Rv32, 0).is_err());
    soc.load_app_at(&prog[..8], Isa::Rv32, 0).unwrap();
    assert_eq!((soc.pc, soc.rom_word(0)), (0, addi(1, 0, 5)));
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
use vux9k_emu::soc::{XMEM_FETCH_FILL, XMEM_FETCH_PROBE, XMEM_LINE_DIRTY, XMEM_LINE_FILL};

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
    // The sw misses the data cache and fills the line; the sb and the loads hit it
    let stores = (3 + XMEM_LINE_FILL + 3) as u64;
    let loads = 3 * 3;
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

/// ROM code that copies `routine` to 0x8000_0000, sets x9 to the return address and
/// jumps there; returns the program and the number of steps up to the jump. The data cache
/// holds the routine dirty (one line up to 8 words): its first fetch waits
/// `XMEM_FETCH_PROBE`, which fills that line, so the others hit.
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
    let p = XMEM_FETCH_PROBE as u64;
    assert_eq!(
        (r.cycle, r.cycles as u64),
        (c0, 2 + p),
        "Retire::cycle is the first FETCH cycle"
    );
    assert_eq!(
        soc.regs[8] as u64,
        mc0 + p + 1,
        "mcycle as read in EXECUTE, after the wait"
    );
    let r = soc.step();
    assert_eq!(
        soc.regs[10] as u64,
        r.cycle + 1,
        "mtime as read in EXECUTE, a hit"
    );
    soc.step();
    assert_eq!(soc.pc, 4 * steps as u32, "returned to the ROM");
    assert_eq!(soc.cycle, c0 + (2 + p) + 2 * 2);
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
        let w = XMEM_FETCH_PROBE as u64; // the first fetch of the stored routine
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
    // A two-instruction routine at 0x8000_0000 (addi x11 += 1; jalr x0, 0(x9)), stored by
    // the ROM (the data cache holds it dirty) and called four times without fence.i:
    // cold (the first fetch's probe writes the data line back and fills the instruction
    // line, so the second hits), warm, after a store to 0x8000_4000 (the routine's
    // instruction-cache line, 16 KB up, and on its data line with another tag: dirty, but
    // no match for the probe), and through the 8 MB alias
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
    w.push(lui(4, 0x80004));
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
    let (m, p) = (XMEM_FETCH_FILL, XMEM_FETCH_PROBE);
    assert_eq!(waits, [p, 0, 0, 0, m, 0, 0, 0]);
    assert_eq!(soc.regs[11], 4);
    let st = soc.caches.stats;
    assert_eq!((st.fetch_hits, st.fetch_misses), (6, 2));
    assert_eq!((st.probe_writebacks, st.writebacks), (1, 0));
    // The power-on reset empties it
    soc.reset();
    soc.pc = 0x8000_0000;
    soc.regs[9] = 0x8000_0004;
    assert_eq!(soc.step().cycles, 2 + m);
}

#[test]
fn xmem_fetch_fills_lines_and_a_store_drops_the_whole_line() {
    // Straight-line code over a line's end: the last word of line 0 and the first of line 1
    // each fill their line. A store to one word of a line then makes the whole line miss
    // once more, and the other line stays
    let at = 0x8000_001Cu32;
    let mut soc = Soc::new(Profile::Real);
    let code = [addi(11, 11, 1), addi(11, 11, 1), addi(11, 11, 1)];
    let bytes: Vec<u8> = code.iter().flat_map(|w| w.to_le_bytes()).collect();
    soc.load_app_at(&bytes, Isa::Rv32, at).unwrap();
    let f = XMEM_FETCH_FILL;
    let wait = |soc: &mut Soc| soc.step().cycles - 2;
    assert_eq!([wait(&mut soc), wait(&mut soc), wait(&mut soc)], [f, f, 0]);
    // A store to word 2 of line 1 (0x28): line 1 goes, line 0 stays
    soc.caches.access(0x28 / 4, true, &vec![0; 0x100]);
    soc.pc = at;
    assert_eq!(wait(&mut soc), 0, "line 0 stays");
    assert_eq!(
        wait(&mut soc),
        XMEM_FETCH_PROBE,
        "line 1, dirty in the data cache"
    );
    assert_eq!(wait(&mut soc), 0);
    // A store a whole instruction-cache size up: the index alone decides, and the data
    // line it dirties has another tag, so the probe finds nothing
    soc.caches
        .access((0x28 + 0x4000) / 4, true, &vec![0; 0x2000]);
    soc.pc = at;
    assert_eq!(wait(&mut soc), 0, "line 0 stays");
    assert_eq!(wait(&mut soc), f);
    assert_eq!(soc.caches.stats.probe_writebacks, 1);
}

#[test]
fn xmem_loads_hit_the_dcache_by_its_rules() {
    // 0x8000_0040 and 0x8000_1040 (4 KB up, another tag) share a data line; a line holds
    // 8 words, so 0x8000_0044 is on it too. A clean victim costs a fill (F), a dirty one
    // its write-back first (D); a store, partial or not, allocates like a load, and the
    // alias 8 MB up is the same line
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000),
            lui(2, 0x80001),
            i(0x03, 0x40, 1, 2, 5), // lw 0x8000_0040: miss, the line was invalid
            i(0x03, 0x44, 1, 2, 5), // lw 0x8000_0044: the same line, a hit
            i(0x03, 0x40, 2, 2, 6), // lw 0x8000_1040: miss, evicts the clean line
            i(0x03, 0x40, 1, 2, 5), // miss, clean victim
            s(0x40, 0, 1, 0),       // sb x0, 0x8000_0040: hit, the line dirty
            i(0x03, 0x40, 1, 2, 5), // hit
            s(0x40, 0, 2, 0),       // sb x0, 0x8000_1040: partial miss, dirty victim
            i(0x03, 0x40, 1, 2, 5), // miss, dirty victim (the sb's line)
            s(0x40, 0, 2, 2),       // sw x0, 0x8000_1040: miss, clean victim
            i(0x03, 0x40, 2, 2, 6), // hit
            lui(4, 0x80801),
            i(0x03, 0x40, 4, 2, 6), // lw through the alias: hit
        ],
    );
    let (f, d) = (XMEM_LINE_FILL, XMEM_LINE_DIRTY);
    let mut waits = vec![];
    for _ in 0..14 {
        let r = soc.step();
        if matches!(r.instr & 0x7F, 0x03 | 0x23) {
            waits.push(r.cycles - 3);
        }
    }
    assert_eq!(waits, [f, 0, f, f, 0, 0, d, d, f, 0, 0]);
    let st = soc.caches.stats;
    assert_eq!((st.load_hits, st.load_misses), (4, 4));
    assert_eq!((st.stores, st.store_hits, st.store_misses), (3, 1, 2));
    assert_eq!((st.writebacks, st.probe_writebacks), (2, 0));
}

#[test]
fn xmem_store_hit_then_load_reads_the_store() {
    // sw (a miss: the line fills, then the word merges), sb into byte 1 and sh into the
    // upper half (hits, partial), lw (a hit) sees all three; a load of another word of
    // the line hits and is untouched
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000),
            lui(2, 0x12345),
            addi(2, 2, 0x678),
            s(0x100, 2, 1, 2), // sw 0x1234_5678
            addi(3, 0, 0xAA),
            s(0x101, 3, 1, 0), // sb 0xAA into byte 1
            addi(3, 0, 0x7BC),
            s(0x102, 3, 1, 1), // sh 0x07BC into the upper half
            i(0x03, 0x100, 1, 2, 5),
            i(0x03, 0x11C, 1, 2, 6),
        ],
    );
    let mut waits = vec![];
    for _ in 0..10 {
        let r = soc.step();
        if matches!(r.instr & 0x7F, 0x03 | 0x23) {
            waits.push(r.cycles - 3);
        }
    }
    assert_eq!(waits, [XMEM_LINE_FILL, 0, 0, 0, 0]);
    assert_eq!((soc.regs[5], soc.regs[6]), (0x07BC_AA78, 0));
    assert_eq!(soc.xmem_word(0x8000_0100), 0x07BC_AA78);
}

#[test]
fn xmem_dirty_lines_survive_a_soft_reset_not_the_power_on_reset() {
    // sw 7 to 0x8000_0100 (a miss: the line fills and is dirty), then the soft reset key
    // (GPIO 0xC); the program runs again from 0 and its sw hits the line the soft reset
    // kept. The power-on reset drops the dirty line: its store never reached the PSRAM
    let prog = [
        lui(1, 0x80000),
        addi(2, 0, 7),
        s(0x100, 2, 1, 2),
        lui(3, 0x40003),
        lui(4, 0xA),
        addi(4, 4, 0x55A),
        s(0xC, 4, 3, 2), // soft reset
    ];
    let mut soc = load(Profile::Real, &prog);
    soc.load_xmem(0x8000_0104, &5u32.to_le_bytes()).unwrap();
    let store_wait = |soc: &mut Soc| {
        run_n(soc, 2);
        soc.step().cycles - 3
    };
    assert_eq!(store_wait(&mut soc), XMEM_LINE_FILL);
    run_n(&mut soc, 4);
    assert_eq!(soc.pc, 0, "soft reset");
    assert_eq!(store_wait(&mut soc), 0, "the line survived the soft reset");
    assert_eq!(soc.caches.stats.store_hits, 1);
    assert_eq!(soc.xmem_word(0x8000_0100), 7);
    // load_xmem writes the PSRAM: a word it writes behind the dirty line stays
    soc.load_xmem(0x8000_0108, &9u32.to_le_bytes()).unwrap();
    soc.reset();
    assert_eq!(
        (soc.xmem_word(0x8000_0100), soc.xmem_word(0x8000_0104)),
        (0, 5),
        "the dirty line's store is lost, the PSRAM's words stay"
    );
    assert_eq!(soc.xmem_word(0x8000_0108), 9);
    assert_eq!(soc.caches.stats, Default::default());
    assert_eq!(store_wait(&mut soc), XMEM_LINE_FILL, "the line is gone");
    // A line written back (here by the fetch's probe) is clean: the reset keeps its words
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000),
            addi(2, 0, 0x13), // nop
            s(0, 2, 1, 2),
            i(0x67, 0, 1, 0, 0), // jalr x0, 0(x1)
        ],
    );
    run_n(&mut soc, 4);
    assert_eq!(soc.step().cycles, 2 + XMEM_FETCH_PROBE);
    soc.reset();
    assert_eq!(soc.xmem_word(0x8000_0000), 0x13);
}

#[test]
fn rv32m_results_and_cycles() {
    // x1 = -7, x2 = 2, x3 = 0; each M instruction takes FETCH, EXECUTE and MULDIV_WAIT
    use vux9k_emu::soc::MULDIV_WAIT_CYCLES;
    let cases: [(u32, u32, u32); 10] = [
        (0, 2, (-14i32) as u32), // mul
        (1, 2, u32::MAX),        // mulh: -14 >> 32
        (2, 2, u32::MAX),        // mulhsu
        (3, 2, 1),               // mulhu: 0xFFFF_FFF9 * 2 >> 32
        (4, 2, (-3i32) as u32),  // div rounds toward zero
        (5, 2, 0x7FFF_FFFC),     // divu
        (6, 2, (-1i32) as u32),  // rem takes the dividend's sign
        (4, 3, u32::MAX),        // div by zero: all ones
        (6, 3, (-7i32) as u32),  // rem by zero: the dividend
        (7, 3, (-7i32) as u32),  // remu by zero
    ];
    for (f3, rs2, want) in cases {
        let mut soc = load(
            Profile::Real,
            &[addi(1, 0, -7), addi(2, 0, 2), r(1, rs2, 1, f3, 4)],
        );
        run_n(&mut soc, 2);
        let r = soc.step();
        assert_eq!(soc.regs[4], want, "funct3 {f3}, rs2 x{rs2}");
        assert_eq!(r.cycles, 2 + MULDIV_WAIT_CYCLES);
        assert_eq!((soc.pc, soc.csr.minstret), (12, 3));
    }
    // The signed overflow: -2^31 / -1
    let mut soc = load(
        Profile::Real,
        &[
            lui(1, 0x80000),
            addi(2, 0, -1),
            r(1, 2, 1, 4, 4),
            r(1, 2, 1, 6, 5),
        ],
    );
    run_n(&mut soc, 4);
    assert_eq!((soc.regs[4], soc.regs[5]), (0x8000_0000, 0));
}

#[test]
fn rv32m_off_traps_and_misa_says_so() {
    let misa = csr(2, 5, 0x301, 0); // csrr x5, misa
    let mut soc = load(Profile::Real, &[misa, r(1, 2, 1, 0, 4)]);
    run_n(&mut soc, 2);
    assert_eq!(soc.regs[5], 0x4000_1100, "RV32, I, M");
    assert_eq!(soc.csr.mcause, 0, "mul is legal");
    let mut soc = load(Profile::Real, &[misa, r(1, 2, 1, 0, 4)]);
    soc.m_ext = false;
    run_n(&mut soc, 2);
    assert_eq!(soc.regs[5], 0x4000_0100, "no M");
    assert_eq!((soc.csr.mcause, soc.csr.mtval), (2, r(1, 2, 1, 0, 4)));
}
