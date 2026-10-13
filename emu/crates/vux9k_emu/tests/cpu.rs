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
fn hack(instrs: &[u16]) -> Vec<u32> {
    let mut v: Vec<u16> = instrs.to_vec();
    if v.len() % 2 == 1 {
        v.push(0xEA87); // 0;JMP (pads the last word; never reached in these tests)
    }
    v.chunks(2)
        .map(|c| (c[1] as u32) << 16 | c[0] as u32)
        .collect()
}
fn c(dest: u16, comp: u16, a: bool, jump: u16) -> u16 {
    0xE000 | (if a { 0x1000 } else { 0 }) | (comp << 6) | (dest << 3) | jump
}

#[test]
fn hack_semantics_and_cycles() {
    // @7, D=A, @100, M=D, @100, D=M+1 (reads M: 4 cycles), AD=D+1 (dual dest: 3 cycles)
    let prog = hack(&[
        7,
        c(0b010, 0b110000, false, 0),
        100,
        c(0b001, 0b001100, false, 0),
        100,
        c(0b010, 0b110111, true, 0),
        c(0b110, 0b011111, false, 0),
    ]);
    let mut soc = load(Profile::Real, &prog);
    run_n(&mut soc, 7);
    assert!(!soc.riscv_mode, "a leading @7 selects Hack");
    assert_eq!(soc.regs[2], 9, "D");
    assert_eq!(soc.regs[1], 9, "A");
    assert_eq!(soc.dram_word_index(100), 7);
    assert_eq!(soc.cycle, 2 + 2 + 2 + 2 + 2 + 4 + 3);
}

#[test]
fn hack_registers_are_32_bit_and_conditions_16_bit() {
    // @0x7FFF, D=A, D=D+1 -> 0x8000 (negative as 16-bit), @4, D;JLT -> jumps to 8 (A*2)
    let prog = hack(&[
        0x7FFF,
        c(0b010, 0b110000, false, 0),
        c(0b010, 0b011111, false, 0),
        4,
        c(0, 0b001100, false, 4),
    ]);
    let mut soc = load(Profile::Real, &prog);
    run_n(&mut soc, 5);
    assert_eq!(soc.regs[2], 0x8000);
    assert_eq!(soc.pc, 8, "JLT on bit 15, target A << 1");
}

#[test]
fn hack_ram_aliases_every_2k_words() {
    // @5, D=A, @0x0805, M=D, @5, D=M
    let prog = hack(&[
        5,
        c(0b010, 0b110000, false, 0),
        0x0805,
        c(0b001, 0b001100, false, 0),
        5,
        c(0b010, 0b110000, true, 0),
    ]);
    let mut soc = load(Profile::Real, &prog);
    run_n(&mut soc, 6);
    assert_eq!(soc.regs[2], 5, "0x0805 is the same word as 0x0005");
}

#[test]
fn isa_detection_skips_zero_words() {
    // First word zero: the detector keeps looking (the CPU runs it as RV and traps)
    let mut soc = load(Profile::Real, &[0, addi(1, 0, 1)]);
    soc.step();
    assert!(soc.riscv_mode);
    assert_eq!(soc.csr.mcause, 2);
}

// ----- application images (load_app, vux9k-emu --load --mode) -------------------------
/// Hack instructions as an `hcc -r` image: big-endian 16-bit words.
fn hack_image(instrs: &[u16]) -> Vec<u8> {
    instrs.iter().flat_map(|i| i.to_be_bytes()).collect()
}

#[test]
fn load_app_starts_hack_in_hack_mode() {
    // @19, D=A, @100, M=D: @19 (0x0013) looks like RV32 OP-IMM to the detector
    let prog = [
        19,
        c(0b010, 0b110000, false, 0),
        100,
        c(0b001, 0b001100, false, 0),
    ];

    let mut soc = Soc::new(Profile::Real);
    soc.load_app(&hack_image(&prog), Isa::Hack).unwrap();
    assert_eq!(
        soc.iram_word(0),
        (c(0b010, 0b110000, false, 0) as u32) << 16 | 19
    );
    run_n(&mut soc, 4);
    assert!(!soc.riscv_mode);
    assert_eq!(soc.dram_word_index(100), 19);

    // The same words left to the ISA detector run as RV32
    let mut soc = load(Profile::Real, &hack(&prog));
    soc.step();
    assert!(soc.riscv_mode, "@19 is detected as RV32I");
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
    // Not real hardware: up to the profile's I-RAM
    let mut soc = Soc::new(Profile::EXTENDED);
    assert!(soc.load_app(&vec![0x13; 256 * 1024], Isa::Rv32).is_ok());
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
    let loads = 3 * (3 + XMEM_READ_LATENCY) as u64;
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
