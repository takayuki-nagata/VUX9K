// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Board self-test (`make hw-smoke`): runs from block RAM in place of the first stage,
//! so it needs neither the SD card nor the boot chain. Each test folds 4096 results of
//! one instruction pattern (xorshift32 operands) into a checksum and compares it with
//! the value the emulator computes (which `make sim-lockstep` ties to the RTL); a
//! bitstream whose timing fails on the board gets them wrong, even when STA passed.
//! The `xmem` tests do the same for the wait-state window (`map::XMEM_BASE`, the 8 MB
//! PSRAM): its address lines and the first and last few KB, every load/store width, code
//! run from it, and the cycles each kind of access waits.
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
/// sign- and zero-extending loads.
fn t_xmem_lanes(a: u32, b: u32) -> u32 {
    let w = xmem_word((b >> 3) as usize & (XMEM_WORDS - 1)) as usize;
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
    // the reference `ret` in I-RAM for t_xmem_time
    "iram_ret:",
    "    ret",
    ".option pop",
);

extern "C" {
    static xmem_copy_start: u32;
    static xmem_code_start: u32;
    static xmem_code_end: u32;
    fn iram_ret();
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

/// Code fetched from the window: the routine above copied there (data reads of I-RAM),
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

static mut TIME_WORD: u32 = 0;

/// Cycles window accesses add over the same access to D-RAM (a load or store) or I-RAM
/// (the fetch of a `ret`), from `cycle` around each, 6 bits each: fetch miss << 24 |
/// fetch hit << 18 | load miss << 12 | load hit << 6 | store. A miss is soc_pkg's
/// XMEM_READ_LATENCY, a hit of the window's caches nothing, a store XMEM_WRITE_LATENCY.
/// The misses are made sure of: storing the `ret` invalidates its instruction-cache line,
/// and a byte store to the loaded word's data-cache line under another tag leaves the line
/// without that word (a hit keeps the other word, a partial miss invalidates).
fn t_xmem_time(_: u32) -> u32 {
    let x = xmem_word(0x40 / 4);
    let xret = xmem_word(0x44 / 4);
    let (c0, c1, c2, c3, c4, c5, c6, c7, c8): (u32, u32, u32, u32, u32, u32, u32, u32, u32);
    unsafe {
        write_volatile(xret, 0x0000_8067); // ret
        fence_i();
        asm!(
            ".option push",
            ".option arch, +zicsr",
            // 4 KB above x, x's data-cache line: whatever it holds, the load of x misses
            "sb zero, 0({xo})",
            "rdcycle {c0}",
            "lw {t}, 0({d})",
            "rdcycle {c1}",
            "lw {t}, 0({x})",
            "rdcycle {c2}",
            "lw {t}, 0({x})",
            "rdcycle {c3}",
            "sw {t}, 0({d})",
            "rdcycle {c4}",
            "sw {t}, 0({x})",
            "rdcycle {c5}",
            "jalr ra, 0({ri})",
            "rdcycle {c6}",
            "jalr ra, 0({rx})",
            "rdcycle {c7}",
            "jalr ra, 0({rx})",
            "rdcycle {c8}",
            ".option pop",
            d = in(reg) core::ptr::addr_of_mut!(TIME_WORD), x = in(reg) x,
            xo = in(reg) x.wrapping_byte_add(0x1000),
            ri = in(reg) iram_ret as *const () as usize, rx = in(reg) xret, t = out(reg) _,
            c0 = out(reg) c0, c1 = out(reg) c1, c2 = out(reg) c2, c3 = out(reg) c3,
            c4 = out(reg) c4, c5 = out(reg) c5, c6 = out(reg) c6, c7 = out(reg) c7,
            c8 = out(reg) c8, out("ra") _)
    };
    let load = c1 - c0;
    let fetch = c6 - c5;
    let field = |v: u32| v & 0x3F;
    field((c7 - c6).wrapping_sub(fetch)) << 24
        | field((c8 - c7).wrapping_sub(fetch)) << 18
        | field((c2 - c1).wrapping_sub(load)) << 12
        | field((c3 - c2).wrapping_sub(load)) << 6
        | field((c5 - c4).wrapping_sub(c4 - c3))
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
const TESTS: [(&str, Test, u32); 15] = [
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
    ("xmem_time", Once(t_xmem_time), 0x2302_3022),
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
