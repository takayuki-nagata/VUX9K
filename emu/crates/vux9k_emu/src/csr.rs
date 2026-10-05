// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Machine-mode CSRs, as soc/cpu/rv32i_csrs.veryl implements them, and the CSR
//! legality rule of soc/cpu/rv32i_trap_unit.veryl (`csr_exists`, `csr_ro_wr`).

pub const MSTATUS: u16 = 0x300;
pub const MISA: u16 = 0x301;
pub const MIE: u16 = 0x304;
pub const MTVEC: u16 = 0x305;
pub const MSTATUSH: u16 = 0x310;
pub const MSCRATCH: u16 = 0x340;
pub const MEPC: u16 = 0x341;
pub const MCAUSE: u16 = 0x342;
pub const MTVAL: u16 = 0x343;
pub const MIP: u16 = 0x344;
pub const MCYCLE: u16 = 0xB00;
pub const MINSTRET: u16 = 0xB02;
pub const MCYCLEH: u16 = 0xB80;
pub const MINSTRETH: u16 = 0xB82;
pub const CYCLE: u16 = 0xC00;
pub const TIME: u16 = 0xC01;
pub const INSTRET: u16 = 0xC02;
pub const CYCLEH: u16 = 0xC80;
pub const TIMEH: u16 = 0xC81;
pub const INSTRETH: u16 = 0xC82;

pub const MISA_VALUE: u32 = 0x4000_0100; // RV32, I
pub const MSTATUS_MIE: u32 = 1 << 3;
pub const MSTATUS_MPIE: u32 = 1 << 7;
const MSTATUS_MPP_M: u32 = 0x1800; // reads as M, never stored
pub const MIP_MSIP: u32 = 1 << 3;
pub const MIP_MTIP: u32 = 1 << 7;
pub const MIP_MEIP: u32 = 1 << 11;

/// Whether the CPU implements `addr` (rv32i_trap_unit's `csr_exists`): the CSRs it
/// keeps, the ones that read 0 (incl. mcountinhibit and the HPM CSRs the spec requires).
pub fn exists(addr: u16) -> bool {
    let hpm = matches!(addr & 0x1F, 3..=0x1F) && matches!(addr >> 5, 0x19 | 0x58 | 0x5C);
    hpm || matches!(
        addr,
        0x300
            | 0x301
            | 0x304
            | 0x305
            | 0x340
            | 0x341
            | 0x342
            | 0x343
            | 0x344
            | 0xB00
            | 0xB02
            | 0xB80
            | 0xB82
            | 0xC00
            | 0xC01
            | 0xC02
            | 0xC80
            | 0xC81
            | 0xC82
            | 0x310
            | 0xF11
            | 0xF12
            | 0xF13
            | 0xF14
            | 0xF15
            | 0x7A0
            | 0x7A1
            | 0x7A2
            | 0x7A3
            | 0x320
    )
}

/// Read-only CSR address space (`addr[11:10] == 3`).
pub fn read_only(addr: u16) -> bool {
    (addr >> 10) & 3 == 3
}

/// The machine CSRs that hold state. The counters' visible values combine the
/// stored counters with the cycle clock; see `Csrs::read`.
#[derive(Clone, Debug, Default)]
pub struct Csrs {
    pub mstatus: u32, // only MIE (bit 3) and MPIE (bit 7) are stored
    pub mie: u32,     // only MSIE/MTIE/MEIE
    pub mtvec: u32,
    pub mscratch: u32,
    pub mepc: u32,
    pub mcause: u32,
    pub mtval: u32,
    pub mcycle: u64,
    pub minstret: u64,
}

impl Csrs {
    /// Value a CSR instruction reads. `mip` is the live pending set, `mtime` the timer.
    pub fn read(&self, addr: u16, mip: u32, mtime: u64) -> u32 {
        match addr {
            MSTATUS => self.mstatus | MSTATUS_MPP_M,
            MISA => MISA_VALUE,
            MIE => self.mie,
            MTVEC => self.mtvec,
            MSCRATCH => self.mscratch,
            MEPC => self.mepc,
            MCAUSE => self.mcause,
            MTVAL => self.mtval,
            MIP => mip,
            MCYCLE | CYCLE => self.mcycle as u32,
            MCYCLEH | CYCLEH => (self.mcycle >> 32) as u32,
            MINSTRET | INSTRET => self.minstret as u32,
            MINSTRETH | INSTRETH => (self.minstret >> 32) as u32,
            TIME => mtime as u32,
            TIMEH => (mtime >> 32) as u32,
            _ => 0,
        }
    }

    /// Store `value` into a writable CSR (after the CSRRW/S/C combine), with the WARL
    /// masks of rv32i_csrs. Writes to read-only and read-as-zero CSRs are ignored.
    pub fn write(&mut self, addr: u16, value: u32) {
        match addr {
            MSTATUS => self.mstatus = value & (MSTATUS_MIE | MSTATUS_MPIE),
            MIE => self.mie = value & (MIP_MSIP | MIP_MTIP | MIP_MEIP),
            MTVEC => self.mtvec = value & !3,
            MSCRATCH => self.mscratch = value,
            MEPC => self.mepc = value & !3,
            MCAUSE => self.mcause = value,
            MTVAL => self.mtval = value,
            MCYCLE => self.mcycle = (self.mcycle & !0xFFFF_FFFF) | value as u64,
            MCYCLEH => self.mcycle = (self.mcycle & 0xFFFF_FFFF) | ((value as u64) << 32),
            MINSTRET => self.minstret = (self.minstret & !0xFFFF_FFFF) | value as u64,
            MINSTRETH => self.minstret = (self.minstret & 0xFFFF_FFFF) | ((value as u64) << 32),
            _ => {} // read-only or read-as-zero: writes are ignored
        }
    }

    /// Trap entry: save the context and disable interrupts.
    pub fn enter_trap(&mut self, pc: u32, cause: u32, tval: u32) {
        self.mepc = pc;
        self.mcause = cause;
        self.mtval = tval;
        let mie = self.mstatus & MSTATUS_MIE != 0;
        self.mstatus = if mie { MSTATUS_MPIE } else { 0 };
    }

    /// MRET: MIE <= MPIE, MPIE <= 1.
    pub fn mret(&mut self) {
        let mpie = self.mstatus & MSTATUS_MPIE != 0;
        self.mstatus = MSTATUS_MPIE | if mpie { MSTATUS_MIE } else { 0 };
    }

    /// The interrupt taken, if any, given the pending lines (rv32i_csrs: MEI > MSI > MTI).
    #[inline]
    pub fn pending_interrupt(&self, mip: u32) -> Option<u32> {
        if self.mstatus & MSTATUS_MIE == 0 {
            return None;
        }
        let pending = self.mie & mip;
        if pending & MIP_MEIP != 0 {
            Some(0x8000_000B)
        } else if pending & MIP_MSIP != 0 {
            Some(0x8000_0003)
        } else if pending & MIP_MTIP != 0 {
            Some(0x8000_0007)
        } else {
            None
        }
    }
}
