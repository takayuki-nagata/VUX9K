// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! sysmon: the system monitor on the board's LEDs
//! (docs/agents/linux.md, "LEDs and the system monitor").
//! Every interval it reads one of /proc/uptime (the CPU's use), /proc/meminfo (the
//! memory in use) or /proc/loadavg (the 1-minute load), and shows it as a bar of up to 4 lit
//! LEDs on led1-led4; the button S2 moves to the next of the three. Its options:
//!
//! ```text
//! sysmon [-m cpu|mem|load] [-i MS] [-b MS] [-n COUNT] [-r KB] [-o ORDER] [-v]
//! ```
//!
//! `-i` the interval (default 1000 ms), `-b` how often the button is read (default 200 ms),
//! `-n` stop after that many intervals, `-v` a line per interval. It writes the LEDs that change,
//! and all four every [`REFRESH_INTERVALS`]. SIGTERM (or SIGINT) turns its
//! LEDs off and ends it with status 0.
//!
//! Every [`RECLAIM_EVERY`] intervals it also reclaims the page cache when no large free block
//! is left ([`Reclaim`]; docs/agents/linux.md, "Memory budget"): /proc/buddyinfo has no free
//! block of `-o` (default [`RECLAIM_ORDER`], busybox's exec) or above (an exec copies the
//! program into one such block, and noMMU has no compaction to make one), and /proc/meminfo's Cached
//! is `-r` kB (default [`RECLAIM_CACHED_KB`]; `-r 0` turns it off) above the Cached its last
//! drop left (the initramfs' pages and whatever else stays; before the first drop, any
//! Cached), so it drops the clean page cache (/proc/sys/vm/drop_caches, `1`). A drop that
//! brings no block of [`BUSYBOX_ORDER`] back (the initramfs' busybox's exec) is followed by
//! [`RECLAIM_HOLDOFF`] intervals without one; one that brings that back but none of `-o` only by
//! the floor. At its start it writes `4` there, which drops nothing but keeps the kernel's
//! `drop_caches: N` line on the console to that one write. `-v` says each drop with the order
//! it looked for; without it a drop says nothing. If the file can't be written it says so once
//! and reclaims no more.
//!
//! This crate is the logic: parsing, the bar, the loop. The system calls are main.c's (uClibc
//! has no Rust target), reached through [`Sys`]; host tests drive the loop with a fake.
//! No `core::fmt` (it would add tens of KB to a program that is copied whole at each exec).
#![cfg_attr(not(test), no_std)]

/// The LEDs sysmon drives: led1 (the bar's first) to led4.
pub const FIRST_LED: u32 = 1;
pub const BAR_LEDS: u32 = 4;

/// What the bar shows.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    Cpu,
    Mem,
    Load,
}

impl Mode {
    pub fn name(self) -> &'static [u8] {
        match self {
            Mode::Cpu => b"cpu",
            Mode::Mem => b"mem",
            Mode::Load => b"load",
        }
    }

    pub fn parse(s: &[u8]) -> Option<Mode> {
        [Mode::Cpu, Mode::Mem, Mode::Load]
            .into_iter()
            .find(|m| m.name() == s)
    }

    /// The next one, as the button steps through them.
    pub fn next(self) -> Mode {
        match self {
            Mode::Cpu => Mode::Mem,
            Mode::Mem => Mode::Load,
            Mode::Load => Mode::Cpu,
        }
    }
}

fn skip_spaces(s: &[u8]) -> &[u8] {
    let n = s.iter().take_while(|&&c| c == b' ' || c == b'\t').count();
    &s[n..]
}

/// A decimal number at the start of s (after spaces), and what follows it.
fn number(s: &[u8]) -> Option<(u64, &[u8])> {
    let s = skip_spaces(s);
    let digits = s.iter().take_while(|c| c.is_ascii_digit()).count();
    if digits == 0 || digits > 19 {
        return None;
    }
    let v = s[..digits]
        .iter()
        .fold(0u64, |v, &c| v * 10 + u64::from(c - b'0'));
    Some((v, &s[digits..]))
}

/// The line of text that starts with key, after the key.
fn line_after<'a>(text: &'a [u8], key: &[u8]) -> Option<&'a [u8]> {
    text.split(|&c| c == b'\n')
        .find(|l| l.starts_with(key))
        .map(|l| &l[key.len()..])
}

/// The CPU's time so far, in hundredths of a second: busy and in all.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct CpuTimes {
    pub busy: u64,
    pub total: u64,
}

/// /proc/uptime, `uptime idle` in seconds with two decimals (one hart: idle never passes the
/// uptime). Far cheaper for the kernel to write than /proc/stat, whose `intr` line counts
/// every interrupt number: sysmon reads it every interval.
pub fn parse_uptime(text: &[u8]) -> Option<CpuTimes> {
    let (up, rest) = decimal(text)?;
    let (idle, _) = decimal(rest)?;
    Some(CpuTimes {
        busy: up.saturating_sub(idle),
        total: up,
    })
}

/// The percentage of the CPU's time between two samples that was busy.
pub fn cpu_percent(prev: CpuTimes, now: CpuTimes) -> u32 {
    let total = now.total.saturating_sub(prev.total);
    let busy = now.busy.saturating_sub(prev.busy).min(total);
    (busy * 100 + total / 2)
        .checked_div(total)
        .map_or(0, |v| v as u32)
}

/// The percentage of the memory in use, from /proc/meminfo's MemTotal and MemFree.
pub fn parse_mem_percent(text: &[u8]) -> Option<u32> {
    let (total, _) = number(line_after(text, b"MemTotal:")?)?;
    let (free, _) = number(line_after(text, b"MemFree:")?)?;
    if total == 0 {
        return None;
    }
    let used = total.saturating_sub(free);
    Some(((used * 100 + total / 2) / total) as u32)
}

/// /proc/meminfo's Cached, in kB (the page cache, the initramfs' ramfs pages included).
pub fn parse_cached_kb(text: &[u8]) -> Option<u64> {
    Some(number(line_after(text, b"Cached:")?)?.0)
}

/// The free blocks of `order` pages or more in /proc/buddyinfo, over all its zones
/// (`Node 0, zone   Normal   n0 n1 ...`: one count per order from 0); None without a zone.
pub fn free_blocks_from(text: &[u8], order: usize) -> Option<u64> {
    let mut zones = 0;
    let mut free = 0u64;
    for line in text.split(|&c| c == b'\n') {
        let mut words = line
            .split(|&c| c == b' ' || c == b'\t')
            .filter(|w| !w.is_empty());
        if words.next() != Some(b"Node") || words.nth(1) != Some(b"zone") || words.next().is_none()
        {
            continue;
        }
        for (k, w) in words.enumerate() {
            match number(w) {
                Some((n, rest)) if rest.is_empty() => {
                    if k >= order {
                        free = free.saturating_add(n);
                    }
                }
                _ => return None,
            }
        }
        zones += 1;
    }
    (zones > 0).then_some(free)
}

/// A decimal number with a fraction at the start of s (after spaces), in hundredths (further
/// digits dropped), and what follows it.
fn decimal(s: &[u8]) -> Option<(u64, &[u8])> {
    let (whole, rest) = number(s)?;
    let rest = rest.strip_prefix(b".")?;
    let digits = rest.iter().take_while(|c| c.is_ascii_digit()).count();
    if digits == 0 {
        return None;
    }
    let frac = (0..2).fold(0u64, |f, k| {
        f * 10
            + if k < digits {
                u64::from(rest[k] - b'0')
            } else {
                0
            }
    });
    Some((whole.checked_mul(100)?.checked_add(frac)?, &rest[digits..]))
}

/// /proc/loadavg's first field (`0.42 0.30 0.20 1/23 456`), in hundredths.
pub fn parse_load(text: &[u8]) -> Option<u32> {
    u32::try_from(decimal(text)?.0).ok()
}

/// How many of the bar's LEDs a value lights: a percentage in quarters (any use lights the
/// first), a load (hundredths) at 0.25, 0.5, 1 and 2.
pub fn bar(mode: Mode, value: u32) -> u32 {
    match mode {
        Mode::Cpu | Mode::Mem => value.min(100).div_ceil(25),
        Mode::Load => [25, 50, 100, 200].iter().filter(|&&t| value >= t).count() as u32,
    }
}

/// What the program was asked to do.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Options {
    pub mode: Mode,
    pub interval_ms: u32,
    pub button_ms: u32,
    /// Intervals until it stops; 0: until a signal
    pub count: u32,
    /// The page cache's reclaim: the Cached it needs, in kB; 0: off
    pub reclaim_kb: u32,
    /// The order of the free block the reclaim keeps
    pub reclaim_order: u32,
    pub verbose: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            mode: Mode::Cpu,
            interval_ms: 1000,
            button_ms: 200,
            count: 0,
            reclaim_kb: RECLAIM_CACHED_KB,
            reclaim_order: RECLAIM_ORDER as u32,
            verbose: false,
        }
    }
}

/// A whole number argument up to max.
fn parse_u32(s: &[u8], max: u32) -> Option<u32> {
    match number(s)? {
        (v, rest) if rest.is_empty() && v <= u64::from(max) => Some(v as u32),
        _ => None,
    }
}

fn parse_ms(s: &[u8]) -> Option<u32> {
    match number(s)? {
        (v, rest) if rest.is_empty() && (1..=3_600_000).contains(&v) => Some(v as u32),
        _ => None,
    }
}

/// The options from the arguments (without the program's name); None for any it doesn't know.
pub fn parse_args<'a>(args: impl IntoIterator<Item = &'a [u8]>) -> Option<Options> {
    let mut o = Options::default();
    let mut it = args.into_iter();
    while let Some(a) = it.next() {
        match a {
            b"-v" => o.verbose = true,
            b"-m" => o.mode = Mode::parse(it.next()?)?,
            b"-i" => o.interval_ms = parse_ms(it.next()?)?,
            b"-b" => o.button_ms = parse_ms(it.next()?)?,
            b"-n" => o.count = parse_u32(it.next()?, u32::MAX)?,
            // More than the RAM is never cached: 8 MB
            b"-r" => o.reclaim_kb = parse_u32(it.next()?, 8192)?,
            // Up to MAX_PAGE_ORDER (buddyinfo's last column); 0 would be any free page
            b"-o" => {
                o.reclaim_order = parse_u32(it.next()?, MAX_ORDER as u32).filter(|&v| v > 0)?
            }
            _ => return None,
        }
    }
    Some(o)
}

/// The LED register's bits sysmon owns for a bar of n LEDs (led1 first).
pub fn bar_leds(n: u32) -> u32 {
    ((1u32 << n.min(BAR_LEDS)) - 1) << FIRST_LED
}

/// A line of output, built without core::fmt (main.c's vux9k_say takes up to 127 bytes).
pub struct Line {
    buf: [u8; 96],
    len: usize,
}

impl Default for Line {
    fn default() -> Self {
        Self::new()
    }
}

impl Line {
    pub fn new() -> Self {
        let mut l = Line {
            buf: [0; 96],
            len: 0,
        };
        l.push(b"sysmon: ");
        l
    }

    pub fn push(&mut self, s: &[u8]) -> &mut Self {
        let n = s.len().min(self.buf.len() - self.len);
        self.buf[self.len..self.len + n].copy_from_slice(&s[..n]);
        self.len += n;
        self
    }

    pub fn num(&mut self, v: u32) -> &mut Self {
        let mut d = [0u8; 10];
        let mut i = d.len();
        let mut v = v;
        loop {
            i -= 1;
            d[i] = b'0' + (v % 10) as u8;
            v /= 10;
            if v == 0 {
                break;
            }
        }
        self.push(&d[i..])
    }

    /// Hundredths as `w.ff`.
    pub fn hundredths(&mut self, v: u32) -> &mut Self {
        self.num(v / 100).push(b".");
        if v % 100 < 10 {
            self.push(b"0");
        }
        self.num(v % 100)
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.buf[..self.len]
    }
}

/// The system calls, main.c's on the board.
pub trait Sys {
    /// The file whole into buf (at most its size); its length, or None.
    fn read_file(&mut self, path: &'static [u8], buf: &mut [u8]) -> Option<usize>;
    /// LED n (0-5) on or off; false if it can't be written.
    fn led(&mut self, n: u32, on: bool) -> bool;
    /// The button, pressed or not; None if there is none.
    fn button(&mut self) -> Option<bool>;
    /// Sleeps; returns early when a signal comes.
    fn sleep_ms(&mut self, ms: u32);
    /// A SIGTERM or SIGINT has come.
    fn stopping(&mut self) -> bool;
    /// One line to stdout (a newline added).
    fn say(&mut self, line: &[u8]);
    /// Writes /proc/sys/vm/drop_caches: `1` drops the clean page cache, `4` only stops the
    /// kernel's line about it after this write; false if it can't be written.
    fn drop_caches(&mut self, what: u8) -> bool;
}

/// The value the mode shows, from its file; the CPU's needs the previous sample.
fn sample(sys: &mut impl Sys, mode: Mode, prev_cpu: &mut Option<CpuTimes>) -> Option<u32> {
    let mut buf = [0u8; 1536];
    match mode {
        Mode::Cpu => {
            let n = sys.read_file(b"/proc/uptime\0", &mut buf)?;
            let now = parse_uptime(&buf[..n])?;
            let v = prev_cpu.map(|p| cpu_percent(p, now));
            *prev_cpu = Some(now);
            v.or(Some(0))
        }
        Mode::Mem => {
            let n = sys.read_file(b"/proc/meminfo\0", &mut buf)?;
            parse_mem_percent(&buf[..n])
        }
        Mode::Load => {
            let n = sys.read_file(b"/proc/loadavg\0", &mut buf)?;
            parse_load(&buf[..n])
        }
    }
}

fn say_mode(sys: &mut impl Sys, o: &Options) {
    let mut l = Line::new();
    l.push(b"mode ").push(o.mode.name());
    sys.say(l.as_bytes());
}

/// Every how many intervals all of the bar's LEDs are written again, not only those that
/// changed: another process (a second sysmon ending, the shell's echo) may have changed one,
/// and a write costs (sysfs, the LED class, the GPIO chip) at 18 MHz.
pub const REFRESH_INTERVALS: u32 = 10;

/// The order of the free block an exec of the initramfs' busybox needs (docs/agents/linux.md,
/// "Memory budget"): 64 pages, 256 KB. A drop that brings back none of it (or of `-o`, if lower)
/// is followed by RECLAIM_HOLDOFF.
pub const BUSYBOX_ORDER: usize = 6;
/// The order of the free block the reclaim keeps by default (`-o`): BUSYBOX_ORDER. Not MPY's
/// exec, order 7 (docs/agents/linux.md, "MicroPython"): what splits the last order-7 block is
/// mostly not the page cache (slab and kernel stacks pin its pageblock), so drops for it would
/// cost the card's programs their cache and bring none back; the card's `python` drops the
/// caches itself before MPY's exec when it needs to. `-o 7` keeps order 7 all the same. A drop
/// may never bring back a block of an `-o` above BUSYBOX_ORDER (the pieces can be another
/// process's): the floor bounds what that costs, not a holdoff, since the exec that waited for
/// the block a drop brought back holds it by the next look.
pub const RECLAIM_ORDER: usize = 6;
/// The largest order /proc/buddyinfo shows (MAX_PAGE_ORDER, its 11 columns from 0)
pub const MAX_ORDER: usize = 10;
/// The Cached (kB) above its floor that a drop needs by default. The initramfs' ramfs pages
/// count in Cached and can't be dropped (~520 kB), so the floor is the Cached its last drop
/// left (what can't be dropped, files written to the ramfs since among it): what lies above it
/// is roughly what a drop frees. 64 kB: the spike's pieces came with 100 to 600 kB of the card's cache above
/// that floor, and RECLAIM_HOLDOFF stops drops that don't help.
pub const RECLAIM_CACHED_KB: u32 = 64;
/// Every how many intervals the reclaim looks at /proc/buddyinfo: each read costs the CPU
/// (~0.15% of it a second at 18 MHz, measured as sysmon's CPU going from 3.6% to 5.1% with a
/// read every interval), and the card's reads that break the blocks take seconds.
pub const RECLAIM_EVERY: u32 = 3;
/// The intervals without a drop after one that brought back no block of BUSYBOX_ORDER: the
/// pieces are then not the page cache's, and a drop at every look would only cost.
pub const RECLAIM_HOLDOFF: u32 = 30;

/// The page cache's reclaim, one step per interval, a look every RECLAIM_EVERY: noMMU has no
/// compaction, and an
/// allocation above order 3 gives up after one try at reclaim, so page cache (the card's
/// reads) left in large blocks fails an exec that MemFree would allow.
#[derive(Default)]
pub struct Reclaim {
    /// /proc/sys/vm/drop_caches can't be written: no more reclaim
    off: bool,
    /// Intervals left without a drop
    holdoff: u32,
    /// A drop at the last look: this one checks what it brought back (the free blocks, which
    /// the pages freed reach in the next seconds)
    dropped: bool,
    /// Intervals so far
    ticks: u32,
    /// The Cached (kB) the last drop left; None before the first
    floor: Option<u64>,
}

impl Reclaim {
    /// At the start: the kernel's `drop_caches` line kept to this one write (`4`). Without
    /// the file (-r 0 aside) the reclaim is off, and it says so once.
    pub fn start(&mut self, sys: &mut impl Sys, cached_kb: u32) {
        if cached_kb != 0 && !sys.drop_caches(b'4') {
            self.fail(sys);
        }
    }

    fn fail(&mut self, sys: &mut impl Sys) {
        self.off = true;
        sys.say(b"sysmon: cannot write /proc/sys/vm/drop_caches");
    }

    /// /proc/meminfo's Cached.
    fn cached(sys: &mut impl Sys, buf: &mut [u8]) -> Option<u64> {
        let n = sys.read_file(b"/proc/meminfo\0", buf)?;
        parse_cached_kb(&buf[..n])
    }

    /// Drops the page cache if no free block of `order` is left and Cached is `cached_kb` above
    /// the floor (any, without one yet); the Cached it dropped at, or None.
    pub fn step(&mut self, sys: &mut impl Sys, cached_kb: u32, order: usize) -> Option<u64> {
        if cached_kb == 0 || self.off {
            return None;
        }
        let look = self.ticks % RECLAIM_EVERY == 0;
        self.ticks = self.ticks.wrapping_add(1);
        if self.holdoff > 0 {
            self.holdoff -= 1;
            return None;
        }
        if !look {
            return None;
        }
        let mut buf = [0u8; 1536];
        let n = sys.read_file(b"/proc/buddyinfo\0", &mut buf)?;
        let large = free_blocks_from(&buf[..n], order)?;
        let needed = free_blocks_from(&buf[..n], order.min(BUSYBOX_ORDER))?;
        if core::mem::take(&mut self.dropped) {
            // Without even busybox's block back; with that but none of `order` (another
            // process holds the pieces, or the exec that waited for it took it) the floor
            // alone stops the next drop until more is cached
            if needed == 0 {
                self.holdoff = RECLAIM_HOLDOFF;
                return None;
            }
        }
        if large > 0 {
            return None;
        }
        let cached = Self::cached(sys, &mut buf)?;
        if self
            .floor
            .is_some_and(|f| cached < f.saturating_add(u64::from(cached_kb)))
        {
            return None;
        }
        if !sys.drop_caches(b'1') {
            self.fail(sys);
            return None;
        }
        // What the drop left (the write returns once it has dropped): the floor, also when it
        // is above the one before (a file written to the ramfs since). Read now, not at the
        // next look: by then the exec that waited for a block has read its file into the cache
        if let Some(left) = Self::cached(sys, &mut buf) {
            self.floor = Some(left);
        }
        self.dropped = true;
        Some(cached)
    }
}

/// Writes the bar's LEDs whose state differs from `lit` (what sysmon wrote last), or all of them
/// when `lit` is None; false if one can't be written.
fn show(sys: &mut impl Sys, lit: &mut Option<u32>, want: u32) -> bool {
    for k in 0..BAR_LEDS {
        let bit = 1 << (FIRST_LED + k);
        if lit.is_none_or(|l| (l ^ want) & bit != 0) && !sys.led(FIRST_LED + k, want & bit != 0) {
            *lit = None;
            return false;
        }
    }
    *lit = Some(want);
    true
}

/// The program: its exit status.
pub fn run(sys: &mut impl Sys, mut o: Options) -> i32 {
    // Whatever the LEDs show, the bar starts dark
    let mut lit = None;
    if !show(sys, &mut lit, 0) {
        sys.say(b"sysmon: cannot write /sys/class/leds/led1-4");
        return 1;
    }
    // First, as init starts it before the shell: the kernel's one drop_caches line comes
    // before the shell's prompt
    let mut reclaim = Reclaim::default();
    reclaim.start(sys, o.reclaim_kb);
    let mut pressed = match sys.button() {
        Some(p) => p,
        None => {
            sys.say(b"sysmon: no button");
            false
        }
    };
    say_mode(sys, &o);
    let mut prev_cpu = None;
    // The CPU's first value needs a sample before it
    if o.mode == Mode::Cpu {
        sample(sys, o.mode, &mut prev_cpu);
    }
    let mut done = 0;
    let status = 'run: loop {
        // One interval, the button read every button_ms
        let mut waited = 0;
        while waited < o.interval_ms {
            let step = o.button_ms.min(o.interval_ms - waited);
            sys.sleep_ms(step);
            waited += step;
            if sys.stopping() {
                break 'run 0;
            }
            if let Some(p) = sys.button() {
                if p && !pressed {
                    o.mode = o.mode.next();
                    say_mode(sys, &o);
                    prev_cpu = None;
                    if o.mode == Mode::Cpu {
                        sample(sys, o.mode, &mut prev_cpu);
                    }
                    // The new mode's value at the end of this interval
                }
                pressed = p;
            }
        }
        let value = sample(sys, o.mode, &mut prev_cpu);
        let n = value.map_or(0, |v| bar(o.mode, v));
        if done % REFRESH_INTERVALS == REFRESH_INTERVALS - 1 {
            lit = None;
        }
        if !show(sys, &mut lit, bar_leds(n)) {
            sys.say(b"sysmon: cannot write /sys/class/leds/led1-4");
            break 'run 1;
        }
        if o.verbose {
            let mut l = Line::new();
            l.push(o.mode.name()).push(b" ");
            match (o.mode, value) {
                (_, None) => l.push(b"?"),
                (Mode::Load, Some(v)) => l.hundredths(v),
                (_, Some(v)) => l.num(v).push(b"%"),
            };
            l.push(b" bar ").num(n);
            sys.say(l.as_bytes());
        }
        if let Some(cached) = reclaim.step(sys, o.reclaim_kb, o.reclaim_order as usize) {
            if o.verbose {
                let mut l = Line::new();
                l.push(b"dropped the page cache (no free block of order ")
                    .num(o.reclaim_order)
                    .push(b", Cached ")
                    .num(cached.min(u64::from(u32::MAX)) as u32)
                    .push(b" kB)");
                sys.say(l.as_bytes());
            }
        }
        done += 1;
        if o.count != 0 && done >= o.count {
            break 'run 0;
        }
    };
    show(sys, &mut None, 0);
    if status == 0 {
        sys.say(b"sysmon: stopped");
    }
    status
}

#[cfg(not(test))]
mod board {
    use super::{parse_args, run, Sys};
    use core::ffi::c_char;

    extern "C" {
        fn vux9k_read_file(path: *const c_char, buf: *mut u8, cap: usize) -> isize;
        fn vux9k_led(n: u32, on: i32) -> i32;
        fn vux9k_button() -> i32;
        fn vux9k_sleep_ms(ms: u32);
        fn vux9k_stopping() -> i32;
        fn vux9k_say(p: *const u8, len: usize);
        fn vux9k_drop_caches(what: u8) -> i32;
        fn _exit(code: i32) -> !;
    }

    struct Board;

    impl Sys for Board {
        fn read_file(&mut self, path: &'static [u8], buf: &mut [u8]) -> Option<usize> {
            let n = unsafe { vux9k_read_file(path.as_ptr().cast(), buf.as_mut_ptr(), buf.len()) };
            usize::try_from(n).ok()
        }
        fn led(&mut self, n: u32, on: bool) -> bool {
            unsafe { vux9k_led(n, on as i32) == 0 }
        }
        fn button(&mut self) -> Option<bool> {
            match unsafe { vux9k_button() } {
                v if v < 0 => None,
                v => Some(v != 0),
            }
        }
        fn sleep_ms(&mut self, ms: u32) {
            unsafe { vux9k_sleep_ms(ms) }
        }
        fn stopping(&mut self) -> bool {
            unsafe { vux9k_stopping() != 0 }
        }
        fn say(&mut self, line: &[u8]) {
            unsafe { vux9k_say(line.as_ptr(), line.len()) }
        }
        fn drop_caches(&mut self, what: u8) -> bool {
            unsafe { vux9k_drop_caches(what) == 0 }
        }
    }

    /// The C string at p.
    unsafe fn arg<'a>(p: *const c_char) -> &'a [u8] {
        let mut n = 0;
        while *p.add(n) != 0 {
            n += 1;
        }
        core::slice::from_raw_parts(p.cast(), n)
    }

    /// main.c's main hands over to this.
    #[no_mangle]
    pub unsafe extern "C" fn sysmon_main(argc: i32, argv: *const *const c_char) -> i32 {
        let args = (1..argc.max(1) as usize).map(|i| arg(*argv.add(i)));
        match parse_args(args) {
            Some(o) => run(&mut Board, o),
            None => {
                let usage =
                    b"usage: sysmon [-m cpu|mem|load] [-i MS] [-b MS] [-n COUNT] [-r KB] [-o ORDER] [-v]";
                vux9k_say(usage.as_ptr(), usage.len());
                2
            }
        }
    }

    #[panic_handler]
    fn panic(_: &core::panic::PanicInfo) -> ! {
        let m = b"sysmon: panic";
        unsafe {
            vux9k_say(m.as_ptr(), m.len());
            _exit(101)
        }
    }
}

#[cfg(test)]
mod tests;
