// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The SoC: CPU (RV32I + Hack), memories and the data-address decode.
//!
//! Every rule here mirrors a line of the RTL, which is the specification:
//! - CPU FSM, cycle costs, writeback: soc/cpu/unified_cpu.veryl
//! - trap priority and legality: soc/cpu/rv32i_trap_unit.veryl
//! - CSRs: soc/cpu/rv32i_csrs.veryl (crate::csr)
//! - Hack decode: soc/cpu/hack_translator.veryl, next PC: soc/cpu/next_pc_unit.veryl
//! - ISA detection: soc/cpu/auto_mode_detector.veryl
//! - address decode and memories: soc/soc_addr_decoder.veryl, soc/soc_ram.veryl,
//!   soc/soc_top.veryl; the IsaTest profile follows sim/tb_hex_runner.veryl.
//!
//! Timing: `cycle` counts 27 MHz clocks since reset release. An instruction starts
//! with its FETCH cycle; `Soc::step` executes one instruction and advances `cycle` by
//! its cost (RV: 2, +1 MEM_WAIT for loads/stores; Hack: 2, +2 when the comp reads M,
//! +1 when it writes both A and D). A trap costs 2 (it is taken in EXECUTE).

use crate::csr::{self, Csrs};
use crate::periph::{self, Periph, SoftReset};
use crate::profile::Profile;

/// riscv-tests' `tohost` (scripts/riscv_tests/link.ld), IsaTest profile only.
pub const TOHOST: u32 = 0xF000_0000;

/// Why `Soc::run` returned.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stop {
    /// The cycle budget ran out.
    Budget,
    /// IsaTest profile: the program stored this value to `TOHOST`.
    ToHost(u32),
}

/// What one executed instruction did (for tracing, lockstep and coverage).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Retire {
    /// Cycle of the instruction's FETCH.
    pub cycle: u64,
    /// True: RV32I, false: Hack.
    pub riscv: bool,
    pub pc: u32,
    pub instr: u32,
    /// Register write (index, value); Hack writes A as x1 and D as x2.
    pub rd: Option<(u8, u32)>,
    /// Second register write (Hack AD=/AMD= write D after A).
    pub rd2: Option<(u8, u32)>,
    /// Data store (address, data as driven on the bus, byte enables).
    pub store: Option<(u32, u32, u8)>,
    /// Trap taken instead of executing (mcause).
    pub trap: Option<u32>,
    /// Conditional branch outcome (RV BRANCH, Hack jump with a condition).
    pub branch_taken: Option<bool>,
    /// Clock cycles the instruction took.
    pub cycles: u32,
}

pub struct Soc {
    pub profile: Profile,
    /// Clock cycles since reset release (the FETCH cycle of the next instruction).
    pub cycle: u64,
    /// Register file: RV x0-x31; in Hack mode A is x1 and D is x2 (unified_cpu).
    pub regs: [u32; 32],
    pub pc: u32,
    pub csr: Csrs,
    pub periph: Periph,
    /// true: RV32I, false: Hack (auto_mode_detector's is_riscv_mode).
    pub riscv_mode: bool,
    mode_latched: bool,
    /// Instructions executed (retired or trapped), for statistics.
    pub steps: u64,
    iram: Vec<u32>,
    dram: Vec<u32>,
    iram_mask: u32,
    dram_mask: u32,
    tohost: Option<u32>,
    /// mcycle value set by a CSR write in the current instruction (replaces the count)
    mcycle_written: Option<u64>,
}

// ----- RV32I field helpers --------------------------------------------------------
#[inline]
fn rd(i: u32) -> usize {
    ((i >> 7) & 31) as usize
}
#[inline]
fn rs1(i: u32) -> usize {
    ((i >> 15) & 31) as usize
}
#[inline]
fn rs2(i: u32) -> usize {
    ((i >> 20) & 31) as usize
}
#[inline]
fn funct3(i: u32) -> u32 {
    (i >> 12) & 7
}
#[inline]
fn funct7(i: u32) -> u32 {
    i >> 25
}
#[inline]
fn imm_i(i: u32) -> u32 {
    ((i as i32) >> 20) as u32
}
#[inline]
fn imm_s(i: u32) -> u32 {
    ((((i as i32) >> 25) << 5) as u32) | ((i >> 7) & 0x1F)
}
#[inline]
fn imm_b(i: u32) -> u32 {
    (((i as i32) >> 31) << 12) as u32 | ((i << 4) & 0x800) | ((i >> 20) & 0x7E0) | ((i >> 7) & 0x1E)
}
#[inline]
fn imm_u(i: u32) -> u32 {
    i & 0xFFFF_F000
}
#[inline]
fn imm_j(i: u32) -> u32 {
    (((i as i32) >> 31) << 20) as u32 | (i & 0xF_F000) | ((i >> 9) & 0x800) | ((i >> 20) & 0x7FE)
}

const OP_LUI: u32 = 0x37;
const OP_AUIPC: u32 = 0x17;
const OP_JAL: u32 = 0x6F;
const OP_JALR: u32 = 0x67;
const OP_BRANCH: u32 = 0x63;
const OP_LOAD: u32 = 0x03;
const OP_STORE: u32 = 0x23;
const OP_IMM: u32 = 0x13;
const OP_REG: u32 = 0x33;
const OP_FENCE: u32 = 0x0F;
const OP_SYSTEM: u32 = 0x73;

/// auto_mode_detector: is `op` one of the RV32I opcodes it recognizes?
fn is_rv32i_opcode(op: u32) -> bool {
    matches!(
        op,
        OP_REG
            | OP_IMM
            | OP_LOAD
            | OP_STORE
            | OP_BRANCH
            | OP_JAL
            | OP_JALR
            | OP_LUI
            | OP_AUIPC
            | OP_SYSTEM
    )
}

/// rv32i_trap_unit's `is_illegal` for an RV32 instruction word.
fn rv_illegal(i: u32) -> bool {
    let f3 = funct3(i);
    let f7 = funct7(i);
    match i & 0x7F {
        OP_LUI | OP_AUIPC | OP_JAL => false,
        OP_JALR => f3 != 0,
        OP_BRANCH => f3 >> 1 == 1,
        OP_LOAD => f3 == 3 || f3 >> 1 == 3,
        OP_STORE => f3 & 4 != 0 || f3 & 3 == 3,
        OP_IMM => match f3 {
            1 => f7 != 0,
            5 => f7 != 0 && f7 != 0x20,
            _ => false,
        },
        OP_REG => !(f7 == 0 || (f7 == 0x20 && (f3 == 0 || f3 == 5))),
        OP_FENCE => f3 >> 1 != 0,
        OP_SYSTEM => {
            if f3 == 0 {
                !matches!(i, 0x0000_0073 | 0x0010_0073 | 0x3020_0073 | 0x1050_0073)
            } else {
                let addr = (i >> 20) as u16;
                let writes = f3 & 3 == 1 || rs1(i) != 0;
                f3 == 4 || (csr::read_only(addr) && writes) || !csr::exists(addr)
            }
        }
        _ => true,
    }
}

impl Soc {
    pub fn new(profile: Profile) -> Soc {
        let iram_words = profile.iram_bytes() / 4;
        let dram_words = profile.dram_bytes().max(4) / 4;
        let mut soc = Soc {
            profile,
            cycle: 0,
            regs: [0; 32],
            pc: 0,
            csr: Csrs::default(),
            periph: Periph::default(),
            riscv_mode: true,
            mode_latched: false,
            steps: 0,
            iram: vec![0; iram_words],
            dram: vec![0; dram_words],
            iram_mask: (iram_words - 1) as u32,
            dram_mask: (dram_words - 1) as u32,
            tohost: None,
            mcycle_written: None,
        };
        soc.reset();
        soc
    }

    /// Power-on reset: CPU and peripherals (memories keep their contents).
    pub fn reset(&mut self) {
        self.regs = [0; 32];
        self.pc = 0;
        self.csr = Csrs::default();
        self.periph = Periph::default();
        self.riscv_mode = true;
        self.mode_latched = false;
        self.cycle = 0;
        self.tohost = None;
    }

    /// cpu_soft_rst (GPIO 0xC): the FSM, PC and CSRs reset, the register file and
    /// everything outside the CPU don't; the CPU fetches from 0 once the pulse ends.
    fn soft_reset(&mut self, sr: SoftReset) {
        self.pc = 0;
        self.csr = Csrs::default();
        self.mcycle_written = None;
        self.cycle = sr.release;
        match sr.mode {
            Some(rv32) => {
                self.riscv_mode = rv32;
                self.mode_latched = true;
            }
            None => {
                self.riscv_mode = true;
                self.mode_latched = false;
            }
        }
    }

    // ----- memory loading (preload, like $readmemh / the loaders) ------------------

    /// Write `bytes` into I-RAM at byte offset `addr` (little-endian words).
    pub fn load_iram(&mut self, addr: u32, bytes: &[u8]) {
        for (n, chunk) in bytes.chunks(4).enumerate() {
            let mut w = [0u8; 4];
            w[..chunk.len()].copy_from_slice(chunk);
            let idx = ((addr / 4) + n as u32) & self.iram_mask;
            self.iram[idx as usize] = u32::from_le_bytes(w);
        }
    }

    /// Write 32-bit words into D-RAM starting at word index 0.
    pub fn load_dram_words(&mut self, words: &[u32]) {
        for (n, w) in words.iter().enumerate() {
            let idx = n as u32 & self.dram_mask;
            self.dram[idx as usize] = *w;
        }
    }

    pub fn iram_word(&self, addr: u32) -> u32 {
        self.iram[((addr >> 2) & self.iram_mask) as usize]
    }

    pub fn dram_word_index(&self, idx: u32) -> u32 {
        self.dram[(idx & self.dram_mask) as usize]
    }

    // ----- fetch and data bus -------------------------------------------------------

    #[inline]
    fn fetch(&self, pc: u32) -> u32 {
        self.iram[((pc >> 2) & self.iram_mask) as usize]
    }

    /// RV32 data read of the 32-bit word containing `addr` (soc_addr_decoder + soc_ram),
    /// by the instruction fetched in cycle `t`.
    fn rv_read_word(&mut self, addr: u32, t: u64) -> u32 {
        if self.profile == Profile::IsaTest {
            return self.iram[((addr >> 2) & self.iram_mask) as usize];
        }
        match addr >> 28 {
            0 | 2 => {
                if self.in_iram_window(addr) {
                    self.iram[((addr >> 2) & self.iram_mask) as usize]
                } else {
                    self.dram[((addr >> 2) & self.dram_mask) as usize]
                }
            }
            4 => self.mmio_read(addr, t),
            _ => 0,
        }
    }

    /// RV32 data store (in MEM_WAIT, the third cycle of the instruction fetched in `t`):
    /// `data` is the lane-replicated store data, `be` the byte enables.
    fn rv_write(&mut self, addr: u32, data: u32, be: u8, t: u64) {
        if self.profile == Profile::IsaTest {
            if addr == TOHOST && self.tohost.is_none() {
                self.tohost = Some(data);
            }
            if addr >> 18 == 0 {
                let idx = ((addr >> 2) & self.iram_mask) as usize;
                self.iram[idx] = merge_bytes(self.iram[idx], data, be);
            }
            return;
        }
        match addr >> 28 {
            // I-RAM takes the whole word: soc_ram has no byte enables on i_mem
            0 if self.in_iram_window(addr) => {
                let idx = ((addr >> 2) & self.iram_mask) as usize;
                self.iram[idx] = data;
            }
            2 => {
                let idx = ((addr >> 2) & self.dram_mask) as usize;
                self.dram[idx] = merge_bytes(self.dram[idx], data, be);
            }
            4 => self.mmio_write(addr, data, t + 2),
            _ => {}
        }
    }

    /// Data addresses that reach I-RAM (the `addr[31:16] == 0` rule of soc_ram/soc_top;
    /// the extended profile widens it to the I-RAM size).
    #[inline]
    fn in_iram_window(&self, addr: u32) -> bool {
        let window = (self.profile.iram_bytes() as u32).max(0x1_0000);
        addr < window
    }

    /// MMIO read by the instruction fetched in cycle `t`. The registered peripherals
    /// (timer, SD, GPIO) sample in its EXECUTE cycle, t + 1.
    fn mmio_read(&mut self, addr: u32, t: u64) -> u32 {
        match (addr >> 12) & 0xF {
            periph::PAGE_TIMER => self.periph.timer.read(addr, t + 1),
            periph::PAGE_GPIO => self.periph.gpio.read(addr, t + 1),
            _ => 0,
        }
    }

    /// MMIO write taking effect at the end of cycle `edge`.
    fn mmio_write(&mut self, addr: u32, data: u32, edge: u64) {
        match (addr >> 12) & 0xF {
            periph::PAGE_TIMER => self.periph.timer.write(addr, data, edge),
            periph::PAGE_GPIO => self.periph.gpio.write(addr, data, edge),
            _ => {}
        }
    }

    /// Hack MMIO addresses (0x6000-0x600F) as RV32 addresses: UART in the first four
    /// words, GPIO above; the register is addr[3:0].
    fn hack_mmio(a16: u32) -> u32 {
        0x4000_0000 | if a16 & 0xC == 0 { 0 } else { 0x3000 } | (a16 & 0xF)
    }

    /// Hack data read of the word at address `a` (Hack decode: RAM below 0x6000), for
    /// the instruction fetched in cycle `t`.
    fn hack_read(&mut self, a: u32, t: u64) -> u32 {
        let a16 = a & 0xFFFF;
        if (0x6000..0x6010).contains(&a16) {
            self.mmio_read(Self::hack_mmio(a16), t)
        } else if a16 < 0x6000 {
            self.dram[(a16 & self.dram_mask) as usize]
        } else {
            0
        }
    }

    /// Hack data write taking effect at the end of cycle `edge`.
    fn hack_write(&mut self, a: u32, v: u32, edge: u64) {
        let a16 = a & 0xFFFF;
        if (0x6000..0x6010).contains(&a16) {
            self.mmio_write(Self::hack_mmio(a16), v, edge);
        } else if a16 < 0x6000 {
            let idx = (a16 & self.dram_mask) as usize;
            self.dram[idx] = v;
        }
    }

    // ----- interrupts -----------------------------------------------------------------

    /// Pending interrupt lines (mip) during cycle `c`.
    fn mip_at(&self, c: u64) -> u32 {
        if self.periph.timer.irq(c) {
            csr::MIP_MTIP
        } else {
            0
        }
    }

    fn mip(&self) -> u32 {
        self.mip_at(self.cycle)
    }

    fn mtime(&self) -> u64 {
        self.periph.timer.mtime(self.cycle)
    }

    // ----- execution ------------------------------------------------------------------

    /// auto_mode_detector, evaluated on the fetched word until it latches.
    fn detect_mode(&mut self, word: u32) {
        if self.mode_latched || word == 0 {
            return;
        }
        if word & 3 == 3 && is_rv32i_opcode(word & 0x7F) {
            self.riscv_mode = true;
            self.mode_latched = true;
        } else if (word >> 13) & 7 == 7 || (word & 0x8000 == 0 && word & 0x7FFF != 0) {
            self.riscv_mode = false;
            self.mode_latched = true;
        }
    }

    /// Execute one instruction (or take a trap) and return what it did.
    pub fn step(&mut self) -> Retire {
        let pc = self.pc;
        let word = self.fetch(pc);
        self.detect_mode(word);
        let mut r = Retire {
            cycle: self.cycle,
            riscv: self.riscv_mode,
            pc,
            ..Retire::default()
        };
        if self.riscv_mode {
            // The interrupt is sampled in FETCH (unified_cpu r_irq)
            let irq = self.csr.pending_interrupt(self.mip());
            self.step_rv(word, irq, &mut r);
        } else {
            self.step_hack(word, &mut r);
        }
        // mcycle counts every clock; a CSR write to it sets the value the next
        // instruction sees instead (rv32i_csrs suppresses that cycle's increment)
        match self.mcycle_written.take() {
            Some(v) => self.csr.mcycle = v,
            None => self.csr.mcycle = self.csr.mcycle.wrapping_add(r.cycles as u64),
        }
        self.cycle += r.cycles as u64;
        self.steps += 1;
        if let Some(sr) = self.periph.gpio.take_soft_reset() {
            self.soft_reset(sr);
        }
        r
    }

    fn write_rd(&mut self, rd: usize, v: u32, r: &mut Retire) {
        if rd != 0 {
            self.regs[rd] = v;
            r.rd = Some((rd as u8, v));
        }
    }

    fn take_trap(&mut self, cause: u32, tval: u32, r: &mut Retire) {
        self.csr.enter_trap(self.pc, cause, tval);
        self.pc = self.csr.mtvec;
        r.trap = Some(cause);
        r.cycles = 2;
    }

    fn step_rv(&mut self, i: u32, irq: Option<u32>, r: &mut Retire) {
        r.instr = i;
        let pc = self.pc;
        let x1 = self.regs[rs1(i)];
        let x2 = self.regs[rs2(i)];
        let op = i & 0x7F;
        let f3 = funct3(i);

        // Exceptions in rv32i_trap_unit's priority order
        if let Some(cause) = irq {
            return self.take_trap(cause, 0, r);
        }
        if rv_illegal(i) {
            return self.take_trap(2, i, r);
        }
        let branch_take = op == OP_BRANCH
            && match f3 {
                0 => x1 == x2,
                1 => x1 != x2,
                4 => (x1 as i32) < (x2 as i32),
                5 => (x1 as i32) >= (x2 as i32),
                6 => x1 < x2,
                7 => x1 >= x2,
                _ => false,
            };
        let jalr_target = x1.wrapping_add(imm_i(i)) & !1;
        let fetch_misalign = match op {
            OP_JAL => imm_j(i) & 2 != 0,
            OP_BRANCH => imm_b(i) & 2 != 0 && branch_take,
            OP_JALR => jalr_target & 2 != 0,
            _ => false,
        };
        if fetch_misalign {
            let target = if op == OP_JALR {
                jalr_target
            } else if op == OP_JAL {
                pc.wrapping_add(imm_j(i))
            } else {
                pc.wrapping_add(imm_b(i))
            };
            return self.take_trap(0, target, r);
        }
        if i == 0x0000_0073 {
            return self.take_trap(11, 0, r);
        }
        if i == 0x0010_0073 {
            return self.take_trap(3, pc, r);
        }
        let mem_addr = x1.wrapping_add(if op == OP_STORE { imm_s(i) } else { imm_i(i) });
        if op == OP_LOAD || op == OP_STORE {
            let misaligned = match f3 & 3 {
                1 => mem_addr & 1 != 0,
                2 => mem_addr & 3 != 0,
                _ => false,
            };
            if misaligned {
                return self.take_trap(if op == OP_LOAD { 4 } else { 6 }, mem_addr, r);
            }
        }

        let mut next = pc.wrapping_add(4);
        r.cycles = 2;
        let mut retired = true;
        match op {
            OP_LUI => self.write_rd(rd(i), imm_u(i), r),
            OP_AUIPC => self.write_rd(rd(i), pc.wrapping_add(imm_u(i)), r),
            OP_JAL => {
                self.write_rd(rd(i), pc.wrapping_add(4), r);
                next = pc.wrapping_add(imm_j(i));
            }
            OP_JALR => {
                self.write_rd(rd(i), pc.wrapping_add(4), r);
                next = jalr_target;
            }
            OP_BRANCH => {
                r.branch_taken = Some(branch_take);
                if branch_take {
                    next = pc.wrapping_add(imm_b(i));
                }
            }
            OP_LOAD => {
                r.cycles = 3;
                let word = self.rv_read_word(mem_addr, self.cycle);
                let sh = (mem_addr & 3) * 8;
                let v = match f3 {
                    0 => ((word >> sh) as u8 as i8) as i32 as u32,
                    4 => (word >> sh) & 0xFF,
                    1 => ((word >> (sh & 16)) as u16 as i16) as i32 as u32,
                    5 => (word >> (sh & 16)) & 0xFFFF,
                    _ => word,
                };
                self.write_rd(rd(i), v, r);
            }
            OP_STORE => {
                r.cycles = 3;
                let lane = mem_addr & 3;
                let (data, be) = match f3 {
                    0 => ((x2 & 0xFF) * 0x0101_0101, 1u8 << lane),
                    1 => (
                        (x2 & 0xFFFF) * 0x0001_0001,
                        if lane & 2 == 0 { 0b0011 } else { 0b1100 },
                    ),
                    _ => (x2, 0b1111),
                };
                self.rv_write(mem_addr, data, be, self.cycle);
                r.store = Some((mem_addr, data, be));
            }
            OP_IMM | OP_REG => {
                let b = if op == OP_IMM { imm_i(i) } else { x2 };
                let sub = op == OP_REG && funct7(i) == 0x20;
                let sh = b & 31;
                let v = match f3 {
                    0 => {
                        if sub {
                            x1.wrapping_sub(b)
                        } else {
                            x1.wrapping_add(b)
                        }
                    }
                    1 => x1 << sh,
                    2 => ((x1 as i32) < (b as i32)) as u32,
                    3 => (x1 < b) as u32,
                    4 => x1 ^ b,
                    5 => {
                        if funct7(i) & 0x20 != 0 {
                            ((x1 as i32) >> sh) as u32
                        } else {
                            x1 >> sh
                        }
                    }
                    6 => x1 | b,
                    _ => x1 & b,
                };
                self.write_rd(rd(i), v, r);
            }
            OP_FENCE => {} // FENCE / FENCE.I: no-ops
            OP_SYSTEM => {
                if f3 == 0 {
                    if i == 0x3020_0073 {
                        self.csr.mret();
                        next = self.csr.mepc;
                    } // WFI: no-op
                } else {
                    retired = self.exec_csr(i, r);
                }
            }
            _ => unreachable!("rv_illegal() rejects every other opcode"),
        }
        if retired {
            self.csr.minstret = self.csr.minstret.wrapping_add(1);
        }
        self.pc = next;
    }

    /// CSRRW/S/C[I]. Returns false when the instruction wrote minstret (the write
    /// replaces its own increment); an mcycle write sets `mcycle_written`.
    fn exec_csr(&mut self, i: u32, r: &mut Retire) -> bool {
        let addr = (i >> 20) as u16;
        let f3 = funct3(i);
        // The instruction reads the counters in its EXECUTE cycle, one after FETCH
        let mut at_execute = self.csr.clone();
        at_execute.mcycle = at_execute.mcycle.wrapping_add(1);
        let old = at_execute.read(addr, self.mip(), self.mtime().wrapping_add(1));
        let src = if f3 & 4 != 0 {
            rs1(i) as u32
        } else {
            self.regs[rs1(i)]
        };
        let writes = f3 & 3 == 1 || rs1(i) != 0;
        let mut counts = true;
        if writes {
            let value = match f3 & 3 {
                1 => src,
                2 => old | src,
                _ => old & !src,
            };
            match addr {
                csr::MCYCLE | csr::MCYCLEH => {
                    // The other half keeps its EXECUTE-cycle value
                    at_execute.write(addr, value);
                    self.mcycle_written = Some(at_execute.mcycle);
                }
                csr::MINSTRET | csr::MINSTRETH => {
                    self.csr.write(addr, value);
                    counts = false;
                }
                _ => self.csr.write(addr, value),
            }
        }
        self.write_rd(rd(i), old, r);
        counts
    }

    fn step_hack(&mut self, word: u32, r: &mut Retire) {
        let pc = self.pc;
        let ins = if pc & 2 != 0 {
            word >> 16
        } else {
            word & 0xFFFF
        };
        r.instr = ins;
        let a = self.regs[1];
        let d = self.regs[2];
        let mut next = pc.wrapping_add(2);
        if ins & 0x8000 == 0 {
            // A-instruction
            self.regs[1] = ins & 0x7FFF;
            r.rd = Some((1, ins & 0x7FFF));
            r.cycles = 2;
        } else {
            let a_bit = ins & 0x1000 != 0;
            let comp = (ins >> 6) & 0x3F;
            let dest = (ins >> 3) & 7;
            let jump = ins & 7;
            // comps that never read M (hack_translator forces use_mem = 0)
            let uses_y = !matches!(comp, 0b001101 | 0b001111 | 0b011111 | 0b001110);
            let use_mem = a_bit && uses_y;
            let t = self.cycle;
            let y = if use_mem { self.hack_read(a, t) } else { a };
            let out = match comp {
                0b101010 => 0,
                0b111111 => 1,
                0b111010 => 0xFFFF_FFFF,
                0b001100 => d,
                0b110000 => y,
                0b001101 => !d,
                0b110001 => !y,
                0b001111 => 0u32.wrapping_sub(d),
                0b110011 => 0u32.wrapping_sub(y),
                0b011111 => d.wrapping_add(1),
                0b110111 => y.wrapping_add(1),
                0b001110 => d.wrapping_sub(1),
                0b110010 => y.wrapping_sub(1),
                0b000010 => d.wrapping_add(y),
                0b010011 => d.wrapping_sub(y),
                0b000111 => y.wrapping_sub(d),
                0b000000 => d & y,
                0b010101 => d | y,
                0b000101 => d ^ y,
                _ => d.wrapping_add(y), // hack_translator default: ADD D, Y
            };
            if dest & 1 != 0 {
                // M is written in EXECUTE, or in HACK_WB when the comp read M
                self.hack_write(a, out, if use_mem { t + 3 } else { t + 1 });
                r.store = Some((a, out, 0b1111));
            }
            if dest & 4 != 0 {
                self.regs[1] = out;
                r.rd = Some((1, out));
            }
            if dest & 2 != 0 {
                self.regs[2] = out;
                if dest & 4 != 0 {
                    r.rd2 = Some((2, out));
                } else {
                    r.rd = Some((2, out));
                }
            }
            let c16 = out & 0xFFFF;
            let zero = c16 == 0;
            let neg = c16 & 0x8000 != 0;
            let take = match jump {
                1 => !zero && !neg,
                2 => zero,
                3 => !neg,
                4 => neg,
                5 => !zero,
                6 => neg || zero,
                7 => true,
                _ => false,
            };
            if jump != 0 {
                if jump != 7 {
                    r.branch_taken = Some(take);
                }
                if take {
                    next = a << 1;
                }
            }
            r.cycles = 2 + if use_mem { 2 } else { 0 } + if dest & 6 == 6 { 1 } else { 0 };
        }
        self.pc = next;
    }

    /// Run until `max_cycles` more clock cycles have elapsed or the program stops.
    pub fn run(&mut self, max_cycles: u64) -> Stop {
        let end = self.cycle.saturating_add(max_cycles);
        while self.cycle < end {
            self.step();
            if let Some(code) = self.tohost {
                return Stop::ToHost(code);
            }
        }
        Stop::Budget
    }
}

#[inline]
fn merge_bytes(old: u32, data: u32, be: u8) -> u32 {
    let mut mask = 0u32;
    for b in 0..4 {
        if be & (1 << b) != 0 {
            mask |= 0xFF << (8 * b);
        }
    }
    (old & !mask) | (data & mask)
}
