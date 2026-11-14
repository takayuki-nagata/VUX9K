// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! sysmon: the system monitor on the board's LEDs
//! (docs/agents/linux.md, "LEDs and the system monitor").
//! Every interval it reads one of /proc/uptime (the CPU's use), /proc/meminfo (the
//! memory in use) or /proc/loadavg (the 1-minute load), and shows it as a bar of up to 4 lit
//! LEDs on led2-led5; the button S2 moves to the next of the three. Its options:
//!
//! ```text
//! sysmon [-m cpu|mem|load] [-i MS] [-b MS] [-n COUNT] [-v]
//! ```
//!
//! `-i` the interval (default 1000 ms), `-b` how often the button is read (default 200 ms),
//! `-n` stop after that many intervals, `-v` a line per interval. It writes the LEDs that change,
//! and all four every [`REFRESH_INTERVALS`]. SIGTERM (or SIGINT) turns its
//! LEDs off and ends it with status 0.
//!
//! This crate is the logic: parsing, the bar, the loop. The system calls are main.c's (uClibc
//! has no Rust target), reached through [`Sys`]; host tests drive the loop with a fake.
//! No `core::fmt` (it would add tens of KB to a program that is copied whole at each exec).
#![cfg_attr(not(test), no_std)]

/// The LEDs sysmon drives: led2 (the bar's first) to led5.
pub const FIRST_LED: u32 = 2;
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
    pub verbose: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            mode: Mode::Cpu,
            interval_ms: 1000,
            button_ms: 200,
            count: 0,
            verbose: false,
        }
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
            b"-n" => {
                o.count = match number(it.next()?)? {
                    (v, rest) if rest.is_empty() && v <= u64::from(u32::MAX) => v as u32,
                    _ => return None,
                }
            }
            _ => return None,
        }
    }
    Some(o)
}

/// The LED register's bits sysmon owns for a bar of n LEDs (led2 first).
pub fn bar_leds(n: u32) -> u32 {
    ((1u32 << n.min(BAR_LEDS)) - 1) << FIRST_LED
}

/// A line of output, built without core::fmt.
pub struct Line {
    buf: [u8; 64],
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
            buf: [0; 64],
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
        sys.say(b"sysmon: cannot write /sys/class/leds/led2-5");
        return 1;
    }
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
            sys.say(b"sysmon: cannot write /sys/class/leds/led2-5");
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
                let usage = b"usage: sysmon [-m cpu|mem|load] [-i MS] [-b MS] [-n COUNT] [-v]";
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
