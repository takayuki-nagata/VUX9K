// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Board self-test (`make hw-smoke`): runs from block RAM in place of the Boot Manager,
//! so it needs neither the SD card nor the boot chain. Each test folds 4096 results of
//! one instruction pattern (xorshift32 operands) into a checksum and compares it with
//! the value the emulator computes (which `make sim-lockstep` ties to the RTL); a
//! bitstream whose timing fails on the board gets them wrong, even when STA passed.
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

const UART_DATA: *mut u32 = map::UART_DATA as *mut u32;
const UART_STATUS: *const u32 = map::UART_STATUS as *const u32;
const MTIME_LOW: *const u32 = map::MTIME_LO as *const u32;
const GPIO_LED: *mut u32 = map::GPIO_LED as *mut u32;

struct Uart;

impl Sink for Uart {
    fn put(&mut self, b: u8) {
        unsafe {
            while read_volatile(UART_STATUS) & map::UART_TX_FULL != 0 {}
            write_volatile(UART_DATA, b as u32);
        }
    }
}

fn set_leds(pattern: u8) {
    unsafe { write_volatile(GPIO_LED, (pattern & 0x3F) as u32) };
}

fn delay_ms(ms: u32) {
    let start = unsafe { read_volatile(MTIME_LOW) };
    let ticks = fw_common::mul_u32(ms, TICKS_PER_MS);
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

/// D-RAM loads and stores of every width, with sign extension.
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

/// (name, test, checksum the emulator computes)
const TESTS: [(&str, fn(u32, u32) -> u32, u32); 11] = [
    ("add", t_add, 0xC02B_9A30),
    ("sub", t_sub, 0x1C9E_5AB3),
    ("logic", t_logic, 0x0F9E_3961),
    ("slt", t_slt, 0xC90C_3502),
    ("shift", t_shift, 0x6E0C_CF4F),
    ("branch", t_branch, 0x351C_F7CF),
    ("subloop", t_subloop, 0x52E7_9264),
    ("subloop_tight", t_subloop_tight, 0x74D7_A346),
    ("raw", t_raw, 0x086D_086D),
    ("mem", t_mem, 0xED19_E792),
    ("regs", t_regs, 0xC561_39A6),
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
        let got = checksum(n as u32, f);
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
