// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Board self-test (`make hw-smoke`): runs from the boot ROM in place of the first stage,
//! so it needs neither the SD card nor the boot chain. Each test folds 4096 results of
//! one instruction pattern (xorshift32 operands) into a checksum and compares it with
//! the value the emulator computes (which `make sim-lockstep` ties to the RTL); a
//! bitstream whose timing fails on the board gets them wrong, even when STA passed.
//! The `xmem` tests do the same for the wait-state window (`map::XMEM_BASE`, the 8 MB
//! PSRAM): its address lines and the first and last few KB, every load/store width, code
//! run from it, and the cycles each kind of access waits. Its own data and stack are in the
//! PSRAM too (link.x: where those tests don't write), so every test also runs through the
//! data cache.
//!
//! Output, one short line each: `T<nn> <name> <checksum> PASS|FAIL`, then
//! `RESULT PASS|FAIL <passed>/<total> <mask>` (hex; bit n-1 of the mask set = test n
//! failed), which is repeated every 2 s: the host's USB-UART bridge can drop part of
//! the output right after the FPGA is programmed, and the repeated line alone carries
//! the verdict.
//! LEDs: 0x01 from the first instruction (start.s), the test number while it runs,
//! then 0x15 <-> 0x2A alternating on PASS, or the first failing test number blinking
//! on FAIL. A trap halts with 0x2A steady.

#![no_std]
#![no_main]

use core::arch::{asm, global_asm};
use core::panic::PanicInfo;
use core::ptr::{read_volatile, write_volatile};

use fw_common::fmt::Sink;
use fw_common::map::{self, TICKS_PER_MS};

global_asm!(include_str!("start.s"));

const UART_RXTX: *mut u32 = map::UART_RXTX as *mut u32;
const UART_TXFULL: *const u32 = map::UART_TXFULL as *const u32;
const MTIME_LOW: *const u32 = map::MTIME_LO as *const u32;
const GPIO_LED: *mut u32 = map::GPIO_LED as *mut u32;

struct Uart;

impl Sink for Uart {
    fn put(&mut self, b: u8) {
        unsafe {
            while read_volatile(UART_TXFULL) != 0 {}
            write_volatile(UART_RXTX, b as u32);
        }
    }
}

fn set_leds(pattern: u8) {
    unsafe { write_volatile(GPIO_LED, (pattern & 0x3F) as u32) };
}

fn delay_ms(ms: u32) {
    let start = unsafe { read_volatile(MTIME_LOW) };
    let ticks = ms * TICKS_PER_MS;
    while unsafe { read_volatile(MTIME_LOW) }.wrapping_sub(start) < ticks {}
}

const N: u32 = 4096;

struct Rng(u32);

impl Rng {
    #[inline(never)]
    fn next(&mut self) -> u32 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        x
    }
}

/// Fold `f(a, b)` over N xorshift pairs (plus equal, sign-flipped and shift-amount
/// operands) into a checksum.
fn checksum(seed: u32, f: fn(u32, u32) -> u32) -> u32 {
    let mut r = Rng(0x1234_5678 ^ seed);
    let mut acc = 0u32;
    for _ in 0..N {
        let a = r.next();
        let b = match r.0 & 7 {
            0 => a,
            1 => a ^ 0x8000_0000,
            2 => r.0 & 0x1F,
            _ => r.next(),
        };
        acc = acc.rotate_left(5) ^ f(a, b);
    }
    acc
}

fn t_add(a: u32, b: u32) -> u32 {
    let r: u32;
    unsafe { asm!("add {r}, {a}, {b}", r = out(reg) r, a = in(reg) a, b = in(reg) b) };
    r
}

fn t_sub(a: u32, b: u32) -> u32 {
    let r: u32;
    unsafe { asm!("sub {r}, {a}, {b}", r = out(reg) r, a = in(reg) a, b = in(reg) b) };
    r
}

fn t_logic(a: u32, b: u32) -> u32 {
    let (x, y, z): (u32, u32, u32);
    unsafe {
        asm!("and {x}, {a}, {b}", "or {y}, {a}, {b}", "xor {z}, {a}, {b}",
             x = out(reg) x, y = out(reg) y, z = out(reg) z, a = in(reg) a, b = in(reg) b)
    };
    x ^ y.rotate_left(11) ^ z.rotate_left(22)
}

fn t_slt(a: u32, b: u32) -> u32 {
    let (x, y): (u32, u32);
    unsafe {
        asm!("slt {x}, {a}, {b}", "sltu {y}, {a}, {b}",
             x = out(reg) x, y = out(reg) y, a = in(reg) a, b = in(reg) b)
    };
    x | (y << 1)
}

fn t_shift(a: u32, b: u32) -> u32 {
    let (x, y, z): (u32, u32, u32);
    unsafe {
        asm!("sll {x}, {a}, {b}", "srl {y}, {a}, {b}", "sra {z}, {a}, {b}",
             x = out(reg) x, y = out(reg) y, z = out(reg) z, a = in(reg) a, b = in(reg) b)
    };
    x ^ y.rotate_left(7) ^ z.rotate_left(19)
}

/// One bit per branch kind: taken or not.
fn t_branch(a: u32, b: u32) -> u32 {
    let r: u32;
    unsafe {
        asm!(
            "li {r}, 0",
            "beq {a}, {b}, 1f", "ori {r}, {r}, 1", "1:",
            "bne {a}, {b}, 1f", "ori {r}, {r}, 2", "1:",
            "blt {a}, {b}, 1f", "ori {r}, {r}, 4", "1:",
            "bge {a}, {b}, 1f", "ori {r}, {r}, 8", "1:",
            "bltu {a}, {b}, 1f", "ori {r}, {r}, 16", "1:",
            "bgeu {a}, {b}, 1f", "ori {r}, {r}, 32", "1:",
            r = out(reg) r, a = in(reg) a, b = in(reg) b)
    };
    r
}

/// fmt::put_dec's inner loop: the value `sub` produces feeds the next compare-branch.
fn t_subloop(a: u32, b: u32) -> u32 {
    let mut v = a & 0xFFFF;
    let p = (b & 0xFF) | 1;
    let mut cnt: u32 = 0;
    unsafe {
        asm!(
            "2:",
            "bltu {v}, {p}, 3f",
            "sub {v}, {v}, {p}",
            "addi {c}, {c}, 1",
            "j 2b",
            "3:",
            v = inout(reg) v, p = in(reg) p, c = inout(reg) cnt)
    };
    cnt ^ (v << 20)
}

/// The same with the dependent branch at the bottom of the loop.
fn t_subloop_tight(a: u32, b: u32) -> u32 {
    let mut v = (a & 0xFFFF) + ((b & 0xFF) | 1);
    let p = (b & 0xFF) | 1;
    let mut cnt: u32 = 0;
    unsafe {
        asm!(
            "2:",
            "sub {v}, {v}, {p}",
            "addi {c}, {c}, 1",
            "bgeu {v}, {p}, 2b",
            v = inout(reg) v, p = in(reg) p, c = inout(reg) cnt)
    };
    cnt ^ (v << 20)
}

/// A producer and its dependent consumer back to back, and one instruction apart.
fn t_raw(a: u32, b: u32) -> u32 {
    let (x, y): (u32, u32);
    unsafe {
        asm!(
            "add {x}, {a}, {b}", "xor {x}, {x}, {a}", "sub {x}, {x}, {b}",
            "add {y}, {a}, {b}", "nop", "xor {y}, {y}, {a}", "nop", "sub {y}, {y}, {b}",
            x = out(reg) x, y = out(reg) y, a = in(reg) a, b = in(reg) b)
    };
    x ^ y.rotate_left(16)
}

static mut BUF: [u32; 64] = [0; 64];

/// Loads and stores of every width, with sign extension, on a buffer in the PSRAM.
fn t_mem(a: u32, b: u32) -> u32 {
    let r: u32;
    unsafe {
        let q = (core::ptr::addr_of_mut!(BUF) as *mut u32).add((b & 63) as usize);
        asm!(
            "sw {a}, 0({q})",
            "lw {r}, 0({q})",
            "sb {b}, 1({q})",
            "lbu {t}, 1({q})", "xor {r}, {r}, {t}",
            "lb {t}, 1({q})", "slli {t}, {t}, 8", "xor {r}, {r}, {t}",
            "sh {b}, 2({q})",
            "lh {t}, 2({q})", "slli {t}, {t}, 3", "xor {r}, {r}, {t}",
            "lw {t}, 0({q})", "slli {t}, {t}, 1", "xor {r}, {r}, {t}",
            a = in(reg) a, b = in(reg) b, q = in(reg) q, r = out(reg) r, t = out(reg) _)
    };
    r
}

/// Register file: a chain through x1, x3..x31 (all but sp), then all of them read back.
fn t_regs(a: u32, b: u32) -> u32 {
    let r: u32;
    unsafe {
        asm!(
            "addi sp, sp, -64",
            "sw s0, 0(sp)", "sw s1, 4(sp)", "sw s2, 8(sp)", "sw s3, 12(sp)",
            "sw s4, 16(sp)", "sw s5, 20(sp)", "sw s6, 24(sp)", "sw s7, 28(sp)",
            "sw s8, 32(sp)", "sw s9, 36(sp)", "sw s10, 40(sp)", "sw s11, 44(sp)",
            "sw gp, 48(sp)", "sw tp, 52(sp)", "sw ra, 56(sp)",
            "mv t0, a0",
            "add t1, t0, a1", "add t2, t1, a1", "add s0, t2, a1", "add s1, s0, a1",
            "add a2, s1, a1", "add a3, a2, a1", "add a4, a3, a1", "add a5, a4, a1",
            "add a6, a5, a1", "add a7, a6, a1", "add s2, a7, a1", "add s3, s2, a1",
            "add s4, s3, a1", "add s5, s4, a1", "add s6, s5, a1", "add s7, s6, a1",
            "add s8, s7, a1", "add s9, s8, a1", "add s10, s9, a1", "add s11, s10, a1",
            "add t3, s11, a1", "add t4, t3, a1", "add t5, t4, a1", "add t6, t5, a1",
            "add gp, t6, a1", "add tp, gp, a1", "add ra, tp, a1",
            "mv a0, t0",
            "xor a0, a0, t1", "xor a0, a0, t2", "xor a0, a0, s0", "xor a0, a0, s1",
            "xor a0, a0, a2", "xor a0, a0, a3", "xor a0, a0, a4", "xor a0, a0, a5",
            "xor a0, a0, a6", "xor a0, a0, a7", "xor a0, a0, s2", "xor a0, a0, s3",
            "xor a0, a0, s4", "xor a0, a0, s5", "xor a0, a0, s6", "xor a0, a0, s7",
            "xor a0, a0, s8", "xor a0, a0, s9", "xor a0, a0, s10", "xor a0, a0, s11",
            "xor a0, a0, t3", "xor a0, a0, t4", "xor a0, a0, t5", "xor a0, a0, t6",
            "slli a2, gp, 1", "xor a0, a0, a2", "slli a2, tp, 2", "xor a0, a0, a2",
            "slli a2, ra, 3", "xor a0, a0, a2",
            "lw s0, 0(sp)", "lw s1, 4(sp)", "lw s2, 8(sp)", "lw s3, 12(sp)",
            "lw s4, 16(sp)", "lw s5, 20(sp)", "lw s6, 24(sp)", "lw s7, 28(sp)",
            "lw s8, 32(sp)", "lw s9, 36(sp)", "lw s10, 40(sp)", "lw s11, 44(sp)",
            "lw gp, 48(sp)", "lw tp, 52(sp)", "lw ra, 56(sp)",
            "addi sp, sp, 64",
            inout("a0") a => r, in("a1") b,
            out("t0") _, out("t1") _, out("t2") _, out("a2") _, out("a3") _, out("a4") _,
            out("a5") _, out("a6") _, out("a7") _, out("t3") _, out("t4") _, out("t5") _,
            out("t6") _)
    };
    r
}

/// RV32M: all eight operations on the pair (the unit takes a fixed 32 steps, so random
/// operands reach its whole datapath)
fn t_muldiv(a: u32, b: u32) -> u32 {
    let (r0, r1, r2, r3, r4, r5, r6, r7): (u32, u32, u32, u32, u32, u32, u32, u32);
    unsafe {
        asm!("mul {r0}, {a}, {b}", "mulh {r1}, {a}, {b}", "mulhsu {r2}, {a}, {b}", "mulhu {r3}, {a}, {b}",
             "div {r4}, {a}, {b}", "divu {r5}, {a}, {b}", "rem {r6}, {a}, {b}", "remu {r7}, {a}, {b}",
             r0 = out(reg) r0, r1 = out(reg) r1, r2 = out(reg) r2, r3 = out(reg) r3,
             r4 = out(reg) r4, r5 = out(reg) r5, r6 = out(reg) r6, r7 = out(reg) r7,
             a = in(reg) a, b = in(reg) b)
    };
    [r0, r1, r2, r3, r4, r5, r6, r7]
        .iter()
        .fold(0, |acc, &x| acc.rotate_left(4) ^ x)
}

/// RV32A: every AMO and an LR/SC pair on a word of the data cache, then an SC with no
/// reservation (the target stays rv32im: the instructions are named for this block only)
fn t_amo(a: u32, b: u32) -> u32 {
    let mut word = a;
    let p = &mut word as *mut u32;
    let mut r = [0u32; 12];
    unsafe {
        asm!(
            ".option push",
            ".option arch, +a",
            "amoadd.w {t}, {b}, ({p})", "sw {t}, 0({r})",
            "amoswap.w {t}, {b}, ({p})", "sw {t}, 4({r})",
            "amoxor.w {t}, {a}, ({p})", "sw {t}, 8({r})",
            "amoor.w {t}, {b}, ({p})", "sw {t}, 12({r})",
            "amoand.w {t}, {a}, ({p})", "sw {t}, 16({r})",
            "amomin.w {t}, {b}, ({p})", "sw {t}, 20({r})",
            "amomax.w {t}, {a}, ({p})", "sw {t}, 24({r})",
            "amominu.w {t}, {b}, ({p})", "sw {t}, 28({r})",
            "amomaxu.w {t}, {a}, ({p})", "sw {t}, 32({r})",
            "lr.w {t}, ({p})", "sw {t}, 36({r})",
            "sc.w {t}, {b}, ({p})", "sw {t}, 40({r})",
            "sc.w {t}, {a}, ({p})", "sw {t}, 44({r})",
            ".option pop",
            t = out(reg) _, a = in(reg) a, b = in(reg) b, p = in(reg) p, r = in(reg) r.as_mut_ptr(),
        )
    };
    r.iter().fold(word, |acc, &x| acc.rotate_left(4) ^ x)
}

// t_umode: into U-mode by mret, where the CSRs and mret trap, an LR/SC and an AMO work, and an
// ecall comes back to M. Every trap folds mcause and mstatus into a0.
global_asm!(
    ".option push",
    ".option arch, +a, +zicsr",
    ".balign 4",
    "umode_run:",
    "csrr t6, mtvec",
    "la t0, umode_trap",
    "csrw mtvec, t0",
    "la t0, umode_user",
    "csrw mepc, t0",
    "li t0, 0x1800",
    "csrc mstatus, t0",
    "mret",
    "umode_user:",
    "csrr t1, mstatus",
    "csrr t1, cycle",
    "csrr t1, mcounteren",
    "mret",
    "addi sp, sp, -4",
    "sw a0, 0(sp)",
    "lr.w t1, (sp)",
    "addi t1, t1, 1",
    "sc.w t2, t1, (sp)",
    "amoadd.w t1, t1, (sp)",
    "xor a0, a0, t1",
    "xor a0, a0, t2",
    "lw t2, 0(sp)",
    "xor a0, a0, t2",
    "addi sp, sp, 4",
    "ecall",
    "umode_back:",
    "csrw mtvec, t6",
    "ret",
    ".balign 4",
    "umode_trap:",
    "csrr t0, mcause",
    "slli t2, a0, 5",
    "srli a0, a0, 27",
    "or a0, a0, t2",
    "xor a0, a0, t0",
    "csrr t2, mstatus",
    "xor a0, a0, t2",
    "li t2, 8",
    "beq t0, t2, 1f",
    "csrr t0, mepc",
    "addi t0, t0, 4",
    "csrw mepc, t0",
    "mret",
    "1:",
    "li t0, 0x1800",
    "csrs mstatus, t0",
    "la t0, umode_back",
    "csrw mepc, t0",
    "mret",
    ".option pop",
);

extern "C" {
    fn umode_run(seed: u32) -> u32;
}

fn t_umode(seed: u32) -> u32 {
    unsafe { umode_run(seed) }
}

// t_clint: the CLINT (map::CLINT_BASE). mtime read hi, lo, lo, hi (hi is 0 in a run's
// first 238 s, the lo reads a fixed number of cycles apart); mtimecmp written and read back;
// MMIO page 1 (UNASSIGNED_PAGE, the TIMER page before the CLINT) read after a store there
// (0) and mtimecmp read again (unchanged); msip set (read back, mip.MSIP), then taken as the machine software interrupt (cause 3),
// whose handler clears it; then mtimecmp a little ahead of mtime and the machine timer
// interrupt (cause 7), whose handler moves mtimecmp out of reach, waited for at most
// 100,000 loops. Every trap folds mcause into a0 and counts in a4; the other values are
// folded as they are read. a1-a3: msip, mtimecmp, mtime.
global_asm!(
    ".option push",
    ".option arch, +zicsr",
    ".macro clint_fold r",
    "slli t0, a0, 5",
    "srli a0, a0, 27",
    "or a0, a0, t0",
    "xor a0, a0, \\r",
    ".endm",
    ".balign 4",
    "clint_run:",
    "csrr t6, mtvec",
    "la t0, clint_trap",
    "csrw mtvec, t0",
    "li a1, {msip}",
    "li a2, {mtimecmp}",
    "li a3, {mtime}",
    "lw t1, 4(a3)",
    "lw t2, 0(a3)",
    "lw t3, 0(a3)",
    "lw t4, 4(a3)",
    "sub t3, t3, t2",
    "clint_fold t1",
    "clint_fold t3",
    "clint_fold t4",
    "li t1, -1",
    "sw t1, 4(a2)",
    "li t1, 0x12345678",
    "sw t1, 0(a2)",
    "lw t1, 0(a2)",
    "clint_fold t1",
    "lw t1, 4(a2)",
    "clint_fold t1",
    // MMIO page 1: unassigned
    "li t2, {page1}",
    "li t1, -1",
    "sw t1, 8(t2)",
    "lw t1, 0(t2)",
    "clint_fold t1",
    "lw t1, 8(t2)",
    "clint_fold t1",
    "lw t1, 0(a2)",
    "clint_fold t1",
    // msip
    "li a4, 0",
    "lw t1, 0(a1)",
    "clint_fold t1",
    "li t1, 8",
    "csrw mie, t1",
    "li t1, 1",
    "sw t1, 0(a1)",
    "nop",
    "lw t1, 0(a1)",
    "clint_fold t1",
    "csrr t1, mip",
    "andi t1, t1, 8",
    "clint_fold t1",
    "csrsi mstatus, 8",
    "nop",
    "csrci mstatus, 8",
    "lw t1, 0(a1)",
    "clint_fold t1",
    "csrr t1, mip",
    "andi t1, t1, 8",
    "clint_fold t1",
    "clint_fold a4",
    // mtimecmp -> MTI
    "li a4, 0",
    "li t1, 0x80",
    "csrw mie, t1",
    "lw t2, 0(a3)",
    "lw t3, 4(a3)",
    "addi t4, t2, 2000",
    "sltu t1, t4, t2",
    "add t3, t3, t1",
    "li t1, -1",
    "sw t1, 4(a2)",
    "sw t4, 0(a2)",
    "sw t3, 4(a2)",
    "csrr t1, mip",
    "andi t1, t1, 0x80",
    "clint_fold t1",
    "li t5, 100000",
    "csrsi mstatus, 8",
    "1:",
    "bnez a4, 2f",
    "addi t5, t5, -1",
    "bnez t5, 1b",
    "2:",
    "csrci mstatus, 8",
    "snez t5, t5",
    "clint_fold t5",
    "clint_fold a4",
    "csrr t1, mip",
    "andi t1, t1, 0x80",
    "clint_fold t1",
    "csrw mie, zero",
    "csrw mtvec, t6",
    "ret",
    ".balign 4",
    "clint_trap:",
    "csrr t1, mcause",
    "clint_fold t1",
    "addi a4, a4, 1",
    "li t2, 0x80000003",
    "bne t1, t2, 1f",
    "sw zero, 0(a1)",
    "mret",
    "1:",
    "li t2, 0x80000007",
    "bne t1, t2, 1f",
    "li t2, -1",
    "sw t2, 4(a2)",
    "mret",
    "1:",
    "csrr t1, mepc",
    "addi t1, t1, 4",
    "csrw mepc, t1",
    "mret",
    ".purgem clint_fold",
    ".option pop",
    msip = const map::CLINT_MSIP,
    mtimecmp = const map::MTIMECMP_LO,
    mtime = const map::MTIME_LO,
    page1 = const UNASSIGNED_PAGE,
);

/// MMIO page 1: unassigned (reads 0, ignores stores) since the CLINT replaced the TIMER page.
const UNASSIGNED_PAGE: usize = 0x4000_1000;

extern "C" {
    fn clint_run(seed: u32) -> u32;
}

fn t_clint(seed: u32) -> u32 {
    unsafe { clint_run(seed) }
}

// t_liteuart: the UART's registers (liteuart's layout, map::UART_*). TXEMPTY polled until the
// FIFO has drained this run's earlier lines (at most 1,000,000 loops; whether it got there
// is folded); then RXEMPTY 1, RXFULL 0, TXFULL 0, EV_STATUS TX only, EV_PENDING TX only
// (EV_STATUS while EV_ENABLE is 0), EV_ENABLE 0, offset 0x20 0 and the 256-byte repeat
// (RXEMPTY, TXEMPTY at +0x100); EV_ENABLE written 3 with MIE clear (read back, EV_PENDING TX
// only, mip.MEIP set), written 2 (EV_PENDING 0: TX hidden, MEIP clear), cleared (MEIP
// clear); then EV_ENABLE = TX with
// MEIE and MIE set: the machine external interrupt (cause 11), whose handler clears EV_ENABLE,
// waited for at most 100,000 loops; EV_ENABLE and MEIP read again. Every trap folds mcause
// into a0 and counts in a4. No byte may arrive on the RX line meanwhile.
global_asm!(
    ".option push",
    ".option arch, +zicsr",
    ".macro uart_fold r",
    "slli t0, a0, 5",
    "srli a0, a0, 27",
    "or a0, a0, t0",
    "xor a0, a0, \\r",
    ".endm",
    ".balign 4",
    "liteuart_run:",
    "csrr t6, mtvec",
    "la t0, liteuart_trap",
    "csrw mtvec, t0",
    "li a1, {uart}",
    "li t5, 1000000",
    "1:",
    "lw t1, {txempty}(a1)",
    "bnez t1, 2f",
    "addi t5, t5, -1",
    "bnez t5, 1b",
    "2:",
    "snez t5, t5",
    "uart_fold t5",
    "lw t1, {rxempty}(a1)",
    "uart_fold t1",
    "lw t1, {rxfull}(a1)",
    "uart_fold t1",
    "lw t1, {txfull}(a1)",
    "uart_fold t1",
    "lw t1, {ev_status}(a1)",
    "uart_fold t1",
    "lw t1, {ev_pending}(a1)",
    "uart_fold t1",
    "lw t1, {ev_enable}(a1)",
    "uart_fold t1",
    "lw t1, 0x20(a1)",
    "uart_fold t1",
    "lw t1, 0x108(a1)",
    "uart_fold t1",
    "lw t1, 0x118(a1)",
    "uart_fold t1",
    // EV_ENABLE with MIE clear: MEIP follows it
    "li a4, 0",
    "li t1, 0x800",
    "csrw mie, t1",
    "li t1, -1",
    "sw t1, {ev_enable}(a1)",
    "lw t1, {ev_enable}(a1)",
    "uart_fold t1",
    "lw t1, {ev_pending}(a1)",
    "uart_fold t1",
    "csrr t1, mip",
    "srli t1, t1, 11",
    "andi t1, t1, 1",
    "uart_fold t1",
    "li t1, 2",
    "sw t1, {ev_enable}(a1)",
    "lw t1, {ev_pending}(a1)",
    "uart_fold t1",
    "csrr t1, mip",
    "srli t1, t1, 11",
    "andi t1, t1, 1",
    "uart_fold t1",
    "sw zero, {ev_enable}(a1)",
    "csrr t1, mip",
    "srli t1, t1, 11",
    "andi t1, t1, 1",
    "uart_fold t1",
    // EV_TX -> MEI
    "li t1, 1",
    "sw t1, {ev_enable}(a1)",
    "li t5, 100000",
    "csrsi mstatus, 8",
    "1:",
    "bnez a4, 2f",
    "addi t5, t5, -1",
    "bnez t5, 1b",
    "2:",
    "csrci mstatus, 8",
    "snez t5, t5",
    "uart_fold t5",
    "uart_fold a4",
    "lw t1, {ev_enable}(a1)",
    "uart_fold t1",
    "csrr t1, mip",
    "srli t1, t1, 11",
    "andi t1, t1, 1",
    "uart_fold t1",
    "csrw mie, zero",
    "csrw mtvec, t6",
    "ret",
    ".balign 4",
    "liteuart_trap:",
    "csrr t1, mcause",
    "uart_fold t1",
    "addi a4, a4, 1",
    "li t2, 0x8000000B",
    "bne t1, t2, 1f",
    "sw zero, {ev_enable}(a1)",
    "mret",
    "1:",
    "csrr t1, mepc",
    "addi t1, t1, 4",
    "csrw mepc, t1",
    "mret",
    ".purgem uart_fold",
    ".option pop",
    uart = const map::UART_RXTX,
    txfull = const map::UART_TXFULL - map::UART_RXTX,
    rxempty = const map::UART_RXEMPTY - map::UART_RXTX,
    ev_status = const map::UART_EV_STATUS - map::UART_RXTX,
    ev_pending = const map::UART_EV_PENDING - map::UART_RXTX,
    ev_enable = const map::UART_EV_ENABLE - map::UART_RXTX,
    txempty = const map::UART_TXEMPTY - map::UART_RXTX,
    rxfull = const map::UART_RXFULL - map::UART_RXTX,
);

extern "C" {
    fn liteuart_run(seed: u32) -> u32;
}

fn t_liteuart(seed: u32) -> u32 {
    unsafe { liteuart_run(seed) }
}

const XMEM_WORDS: usize = map::XMEM_BYTES / 4;

fn xmem_word(i: usize) -> *mut u32 {
    (map::XMEM_BASE + i * 4) as *mut u32
}

/// An address-dependent word (xorshift of the address).
fn xmem_pattern(addr: u32, seed: u32) -> u32 {
    let mut x = addr ^ 0xA5C3_0F96 ^ seed;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    x
}

/// Words in each of the blocks at the window's start and end that `t_xmem` tests whole.
const XMEM_BLOCK_WORDS: usize = 512;
/// Word-address bits of the window (soc_pkg::XMEM_ADDR_BITS).
const XMEM_ADDR_BITS: usize = XMEM_WORDS.trailing_zeros() as usize;

/// Writes f(addr) to the word at `top ^ (4 << b)` for every word-address bit b, and to
/// `top` itself, then reads them all back: an address line stuck or shorted to another
/// makes two of these words one, and a value is lost (the checksum changes).
fn xmem_lines(top: usize, seed: u32, mut acc: u32) -> u32 {
    let at = |b: usize| -> *mut u32 {
        let flip = if b < XMEM_ADDR_BITS { 4 << b } else { 0 };
        (top ^ flip) as *mut u32
    };
    unsafe {
        for b in 0..=XMEM_ADDR_BITS {
            write_volatile(at(b), xmem_pattern(at(b) as u32, seed));
        }
        for b in 0..=XMEM_ADDR_BITS {
            acc = acc.rotate_left(5).wrapping_add(read_volatile(at(b)));
        }
    }
    acc
}

/// The window's 21 word-address lines, walking one (from the base) and walking zero (from
/// its last word, 0x807F_FFFC); the register space hidden (a store through 0x8080_0020,
/// which would be the PSRAM's CR0 if address bit 23 reached the controller, lands in
/// 0x8000_0020; one through the top of region 8 in the last word); then the first and
/// the last XMEM_BLOCK_WORDS words in two passes of writing all then reading all: f(addr),
/// then !f(addr) read through the alias 8 MB up. About 4,200 window accesses (~10 ms).
fn t_xmem(seed: u32) -> u32 {
    let top = map::XMEM_BASE + map::XMEM_BYTES - 4;
    let mut acc = xmem_lines(map::XMEM_BASE, seed, 0);
    acc = xmem_lines(top, seed, acc);
    unsafe {
        let cr0 = xmem_word(0x20 / 4);
        write_volatile(cr0.byte_add(map::XMEM_BYTES), 0x8080_0020 ^ seed);
        acc = acc.rotate_left(5).wrapping_add(read_volatile(cr0));
        write_volatile(0x8FFF_FFFC as *mut u32, 0x8FFF_FFFC ^ seed);
        acc = acc
            .rotate_left(5)
            .wrapping_add(read_volatile(top as *const u32));
    }
    let block = |k: usize| -> *mut u32 {
        let i = if k < XMEM_BLOCK_WORDS {
            k
        } else {
            XMEM_WORDS - 2 * XMEM_BLOCK_WORDS + k
        };
        xmem_word(i)
    };
    // added, not xored: !f cancels f in an xor over all words
    unsafe {
        for k in 0..2 * XMEM_BLOCK_WORDS {
            write_volatile(block(k), xmem_pattern(block(k) as u32, seed));
        }
        for k in 0..2 * XMEM_BLOCK_WORDS {
            acc = acc.rotate_left(5).wrapping_add(read_volatile(block(k)));
        }
        for k in 0..2 * XMEM_BLOCK_WORDS {
            write_volatile(block(k), !xmem_pattern(block(k) as u32, seed));
        }
        for k in 0..2 * XMEM_BLOCK_WORDS {
            acc = acc
                .rotate_left(5)
                .wrapping_add(read_volatile(block(k).byte_add(map::XMEM_BYTES)));
        }
    }
    acc
}

/// Window loads and stores of every width: a byte and a halfword stored into a word
/// with known contents (the window merges the lanes), read back as a word and by
/// sign- and zero-extending loads. Words in the lower 4 MB: this program's data and stack
/// are in the upper half.
fn t_xmem_lanes(a: u32, b: u32) -> u32 {
    let w = xmem_word((b >> 3) as usize & (XMEM_WORDS / 2 - 1)) as usize;
    let lane = (b & 3) as usize;
    let half = (b & 2) as usize;
    unsafe {
        write_volatile(w as *mut u32, a);
        write_volatile((w + lane) as *mut u8, (b >> 8) as u8);
        let r = read_volatile(w as *const u32);
        write_volatile((w + (half ^ 2)) as *mut u16, (b >> 16) as u16);
        let s = read_volatile(w as *const u32);
        let c = read_volatile((w + (lane ^ 1)) as *const i8) as u32;
        let d = read_volatile((w + (lane ^ 2)) as *const u8) as u32;
        let e = read_volatile((w + half) as *const i16) as u32;
        let f = read_volatile((w + (half ^ 2)) as *const u16) as u32;
        r ^ s.rotate_left(8)
            ^ c.rotate_left(13)
            ^ d.rotate_left(17)
            ^ e.rotate_left(3)
            ^ f.rotate_left(24)
    }
}

// Copied into the window and run there by t_xmem_exec (position independent): a loop
// with a backward branch, a jal and auipc (the address it runs at), and a store and
// load of the data word in front of it. Called at xmem_code_start as fn(a, b) -> u32.
global_asm!(
    ".section .text.xmem_code, \"ax\"",
    ".option push",
    ".option norelax",
    ".balign 4",
    "xmem_copy_start:",
    "3:  .word 0",
    "xmem_code_start:",
    "    andi t0, a1, 7",
    "1:  slli t1, a0, 3",
    "    srli t2, a0, 29",
    "    or a0, t1, t2",
    "    xor a0, a0, a1",
    "    addi a1, a1, 0x35",
    "    addi t0, t0, -1",
    "    bgez t0, 1b",
    "    jal t1, 2f",
    "2:  auipc t2, %pcrel_hi(3b)",
    "    sub t1, t2, t1",
    "    add a0, a0, t1",
    "    xor a0, a0, t2",
    "    sw a0, %pcrel_lo(2b)(t2)",
    "    lw t1, %pcrel_lo(2b)(t2)",
    "    slli t1, t1, 1",
    "    xor a0, a0, t1",
    "    ret",
    "xmem_code_end:",
    // the reference `ret` in the ROM for t_xmem_time and t_xmem_time_line
    "rom_ret:",
    "    ret",
    ".option pop",
);

extern "C" {
    static xmem_copy_start: u32;
    static xmem_code_start: u32;
    static xmem_code_end: u32;
    fn rom_ret();
}

/// Where t_xmem_exec puts its routine.
const XMEM_CODE_OFF: usize = 0x100;

/// The window routine, called at the base or (b bit 8) through the alias 8 MB up.
fn xmem_call(a: u32, b: u32) -> u32 {
    let up = if b & 0x100 != 0 { map::XMEM_BYTES } else { 0 };
    let entry = core::ptr::addr_of!(xmem_code_start) as usize
        - core::ptr::addr_of!(xmem_copy_start) as usize;
    let f: extern "C" fn(u32, u32) -> u32 =
        unsafe { core::mem::transmute(map::XMEM_BASE + up + XMEM_CODE_OFF + entry) };
    f(a, b)
}

fn fence_i() {
    unsafe {
        asm!(
            ".option push",
            ".option arch, +zifencei",
            "fence.i",
            ".option pop"
        )
    };
}

/// Code fetched from the window: the routine above copied there (data reads of the ROM),
/// `fence.i`, then folded over xorshift pairs like the instruction tests.
fn t_xmem_exec(seed: u32) -> u32 {
    unsafe {
        let start = core::ptr::addr_of!(xmem_copy_start);
        let words = core::ptr::addr_of!(xmem_code_end).offset_from(start) as usize;
        for i in 0..words {
            write_volatile(
                xmem_word(XMEM_CODE_OFF / 4 + i),
                read_volatile(start.add(i)),
            );
        }
    }
    fence_i();
    checksum(seed, xmem_call)
}

/// Cycles window accesses add over the same access to the ROM (a load, a store, which the
/// ROM ignores, or the fetch of a `ret`), from `cycle` around each, 6 bits each: fetch miss
/// << 18 | fetch hit << 12 | load hit << 6 | store hit. A fetch miss is the instruction
/// line's fill, XMEM_FETCH_FILL (the emulator's soc.rs: 8 words in a burst, 49 from soc_pkg's
/// XMEM_READ_LATENCY), a hit of either cache nothing (a store hit stays in the data cache).
/// Each case is made by construction, inside one asm block (no stack access in between):
/// the stored `ret` invalidates its whole instruction-cache line and dirties x's data line; a
/// load 2 KB up (the same data line, another tag) writes it back and holds the line clean,
/// so the load and the store after it hit; the fetch miss then probes a dirty line of
/// another tag, which costs nothing.
fn t_xmem_time(_: u32) -> u32 {
    let x = xmem_word(0x40 / 4);
    let xret = xmem_word(0x44 / 4);
    let (c0, c1, c2, c3, c4, c5, c6, c7): (u32, u32, u32, u32, u32, u32, u32, u32);
    unsafe {
        asm!(
            ".option push",
            ".option arch, +zicsr",
            "sw {ret}, 0({rx})",
            "lw {t}, 0({xo})",
            "rdcycle {c0}",
            "lw {t}, 0({d})",
            "rdcycle {c1}",
            "lw {t}, 0({xo})",
            "rdcycle {c2}",
            "sw zero, 0({d})",
            "rdcycle {c3}",
            "sw zero, 0({xo})",
            "rdcycle {c4}",
            "jalr ra, 0({d})",
            "rdcycle {c5}",
            "jalr ra, 0({rx})",
            "rdcycle {c6}",
            "jalr ra, 0({rx})",
            "rdcycle {c7}",
            ".option pop",
            d = in(reg) rom_ret as *const () as usize, rx = in(reg) xret,
            xo = in(reg) x.wrapping_byte_add(0x800), ret = in(reg) 0x0000_8067u32,
            t = out(reg) _,
            c0 = out(reg) c0, c1 = out(reg) c1, c2 = out(reg) c2, c3 = out(reg) c3,
            c4 = out(reg) c4, c5 = out(reg) c5, c6 = out(reg) c6, c7 = out(reg) c7,
            out("ra") _)
    };
    let load = c1 - c0;
    let fetch = c5 - c4;
    let field = |v: u32| v & 0x3F;
    field((c6 - c5).wrapping_sub(fetch)) << 18
        | field((c7 - c6).wrapping_sub(fetch)) << 12
        | field((c2 - c1).wrapping_sub(load)) << 6
        | field((c4 - c3).wrapping_sub(c3 - c2))
}

/// The line transfers' cycles over the same access to the ROM, as t_xmem_time measures
/// them, 10 bits each: dirty miss << 20 | clean miss << 10 | probed fetch. A load that
/// misses with a dirty victim waits XMEM_LINE_DIRTY (the emulator's soc.rs: the victim's 8
/// words written back in a burst, then the 8 read in one), one with a clean victim XMEM_LINE_FILL, and a
/// fetch miss whose line the data cache holds dirty XMEM_FETCH_PROBE (the line written
/// back first, without fence.i, then the instruction line's fill). By construction, in one asm block: a store 2 KB above y
/// makes y's data line dirty under another tag, whatever it held; the load of y then
/// evicts it, the load 2 KB up evicts y's clean line, and the stored `ret` next to y (a
/// store miss of a clean victim) leaves its line dirty and its instruction-cache line
/// invalid.
fn t_xmem_time_line(_: u32) -> u32 {
    let y = xmem_word(0x80 / 4);
    let yret = xmem_word(0x84 / 4);
    let (c0, c1, c2, c3, c4, c5, c6): (u32, u32, u32, u32, u32, u32, u32);
    unsafe {
        asm!(
            ".option push",
            ".option arch, +zicsr",
            "sw zero, 0({yo})",
            "rdcycle {c0}",
            "lw {t}, 0({d})",
            "rdcycle {c1}",
            "lw {t}, 0({y})",
            "rdcycle {c2}",
            "lw {t}, 0({yo})",
            "rdcycle {c3}",
            "sw {ret}, 0({yr})",
            "rdcycle {c4}",
            "jalr ra, 0({d})",
            "rdcycle {c5}",
            "jalr ra, 0({yr})",
            "rdcycle {c6}",
            ".option pop",
            d = in(reg) rom_ret as *const () as usize, y = in(reg) y, yr = in(reg) yret,
            yo = in(reg) y.wrapping_byte_add(0x800), ret = in(reg) 0x0000_8067u32,
            t = out(reg) _,
            c0 = out(reg) c0, c1 = out(reg) c1, c2 = out(reg) c2, c3 = out(reg) c3,
            c4 = out(reg) c4, c5 = out(reg) c5, c6 = out(reg) c6, out("ra") _)
    };
    let load = c1 - c0;
    let field = |v: u32| v & 0x3FF;
    field((c2 - c1).wrapping_sub(load)) << 20
        | field((c3 - c2).wrapping_sub(load)) << 10
        | field((c6 - c5).wrapping_sub(c5 - c4))
}

/// A test folds `f(a, b)` over xorshift pairs (`checksum`), or runs once (given its
/// test number as a seed).
#[derive(Clone, Copy)]
enum Test {
    Pairs(fn(u32, u32) -> u32),
    Once(fn(u32) -> u32),
}

use Test::{Once, Pairs};

/// (name, test, checksum the emulator computes)
const TESTS: [(&str, Test, u32); 21] = [
    ("add", Pairs(t_add), 0xC02B_9A30),
    ("sub", Pairs(t_sub), 0x1C9E_5AB3),
    ("logic", Pairs(t_logic), 0x0F9E_3961),
    ("slt", Pairs(t_slt), 0xC90C_3502),
    ("shift", Pairs(t_shift), 0x6E0C_CF4F),
    ("branch", Pairs(t_branch), 0x351C_F7CF),
    ("subloop", Pairs(t_subloop), 0x52E7_9264),
    ("subloop_tight", Pairs(t_subloop_tight), 0x74D7_A346),
    ("raw", Pairs(t_raw), 0x086D_086D),
    ("mem", Pairs(t_mem), 0xED19_E792),
    ("regs", Pairs(t_regs), 0xC561_39A6),
    ("xmem", Once(t_xmem), 0xDB16_358C),
    ("xmem_lanes", Pairs(t_xmem_lanes), 0x4490_6ED7),
    ("xmem_exec", Once(t_xmem_exec), 0x9273_B7CB),
    ("xmem_time", Once(t_xmem_time), 0x00C4_0000),
    ("xmem_time_line", Once(t_xmem_time_line), 0x0630_C464),
    ("muldiv", Pairs(t_muldiv), 0x180C_F09F),
    ("amo", Pairs(t_amo), 0x29F4_9444),
    ("umode", Once(t_umode), 0x4C42_10E8),
    ("clint", Once(t_clint), 0x26A2_6E4C),
    ("liteuart", Once(t_liteuart), 0x9035_8710),
];

#[no_mangle]
pub extern "C" fn main() -> ! {
    Uart.put_str("\nhw_test\n");
    let mut passed = 0u8;
    let mut first_fail = 0u8;
    let mut fail_mask = 0u32;
    for (i, &(name, f, expected)) in TESTS.iter().enumerate() {
        let n = i as u8 + 1;
        set_leds(n);
        let got = match f {
            Pairs(f) => checksum(n as u32, f),
            Once(f) => f(n as u32),
        };
        Uart.put_str("T");
        Uart.put_hex_byte(n);
        Uart.put_str(" ");
        Uart.put_str(name);
        Uart.put_str(" ");
        Uart.put_hex(got);
        if got == expected {
            passed += 1;
            Uart.put_str(" PASS\n");
        } else {
            if first_fail == 0 {
                first_fail = n;
            }
            fail_mask |= 1 << i;
            Uart.put_str(" FAIL\n");
        }
    }
    let ok = first_fail == 0;
    let (on, off) = if ok { (0x15, 0x2A) } else { (first_fail, 0) };
    loop {
        Uart.put_str(if ok { "RESULT PASS " } else { "RESULT FAIL " });
        Uart.put_hex_byte(passed);
        Uart.put_str("/");
        Uart.put_hex_byte(TESTS.len() as u8);
        Uart.put_str(" ");
        Uart.put_hex(fail_mask);
        Uart.put_str("\n");
        for _ in 0..4 {
            set_leds(on);
            delay_ms(250);
            set_leds(off);
            delay_ms(250);
        }
    }
}

#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
    set_leds(0x2A);
    loop {}
}
