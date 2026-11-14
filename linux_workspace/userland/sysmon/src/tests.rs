// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests (make test-sysmon-host): the parsers on the kernel's own text, the bar, the
//! options, and the loop against a fake system.

use super::*;
use std::collections::VecDeque;

const MEMINFO: &[u8] =
    b"MemTotal:           5124 kB\nMemFree:            3228 kB\nMemAvailable:       3500 kB\n";

#[test]
fn uptime_less_idle_is_busy() {
    assert_eq!(
        parse_uptime(b"12.34 10.30\n"),
        Some(CpuTimes {
            busy: 204,
            total: 1234
        })
    );
    assert_eq!(
        parse_uptime(b"5.5 5.505\n"),
        Some(CpuTimes {
            busy: 0,
            total: 550
        })
    );
    assert_eq!(parse_uptime(b"12.34\n"), None);
    assert_eq!(parse_uptime(b"12 10\n"), None);
    assert_eq!(parse_uptime(b"9999999999999999999.00 1.00"), None);
}

#[test]
fn cpu_percent_between_samples() {
    let a = CpuTimes {
        busy: 200,
        total: 1000,
    };
    assert_eq!(
        cpu_percent(
            a,
            CpuTimes {
                busy: 250,
                total: 1100
            }
        ),
        50
    );
    assert_eq!(
        cpu_percent(
            a,
            CpuTimes {
                busy: 300,
                total: 1100
            }
        ),
        100
    );
    assert_eq!(cpu_percent(a, a), 0);
    // Counters that go backwards (never in practice) clamp, not wrap
    assert_eq!(
        cpu_percent(
            a,
            CpuTimes {
                busy: 100,
                total: 900
            }
        ),
        0
    );
    assert_eq!(
        cpu_percent(
            a,
            CpuTimes {
                busy: 999,
                total: 1001
            }
        ),
        100
    );
}

#[test]
fn memory_in_use() {
    assert_eq!(parse_mem_percent(MEMINFO), Some(37));
    assert_eq!(parse_mem_percent(b"MemTotal: 0 kB\nMemFree: 0 kB\n"), None);
    assert_eq!(parse_mem_percent(b"MemFree: 1 kB\n"), None);
    assert_eq!(
        parse_mem_percent(b"MemTotal: 100 kB\nMemFree: 200 kB\n"),
        Some(0)
    );
}

#[test]
fn load_in_hundredths() {
    assert_eq!(parse_load(b"0.42 0.30 0.20 1/23 456\n"), Some(42));
    assert_eq!(parse_load(b"12.05 0 0"), Some(1205));
    assert_eq!(parse_load(b"1.5 0 0"), Some(150));
    assert_eq!(parse_load(b"3 0 0"), None);
    assert_eq!(parse_load(b"3. 0"), None);
    assert_eq!(parse_load(b""), None);
}

#[test]
fn bars() {
    let cpu: Vec<u32> = [0, 1, 25, 26, 50, 51, 75, 76, 100, 150]
        .iter()
        .map(|&v| bar(Mode::Cpu, v))
        .collect();
    assert_eq!(cpu, [0, 1, 1, 2, 2, 3, 3, 4, 4, 4]);
    assert_eq!(bar(Mode::Mem, 37), 2);
    let load: Vec<u32> = [0, 24, 25, 49, 50, 99, 100, 199, 200, 900]
        .iter()
        .map(|&v| bar(Mode::Load, v))
        .collect();
    assert_eq!(load, [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
    assert_eq!(bar_leds(0), 0);
    assert_eq!(bar_leds(1), 0b000100);
    assert_eq!(bar_leds(4), 0b111100);
    assert_eq!(bar_leds(9), 0b111100);
}

fn args(s: &str) -> Option<Options> {
    parse_args(s.split_whitespace().map(str::as_bytes))
}

#[test]
fn options() {
    assert_eq!(args(""), Some(Options::default()));
    let o = args("-m load -i 500 -b 50 -n 3 -v").unwrap();
    assert_eq!(
        o,
        Options {
            mode: Mode::Load,
            interval_ms: 500,
            button_ms: 50,
            count: 3,
            verbose: true
        }
    );
    for bad in [
        "-m",
        "-m disk",
        "-i 0",
        "-i x",
        "-i 10x",
        "-b",
        "-n -1",
        "-n 99999999999",
        "-x",
        "cpu",
    ] {
        assert_eq!(args(bad), None, "{bad}");
    }
    assert_eq!(Mode::Cpu.next().next().next(), Mode::Cpu);
}

#[test]
fn lines() {
    let mut l = Line::new();
    l.num(0)
        .push(b" ")
        .num(4_294_967_295)
        .push(b" ")
        .hundredths(5)
        .push(b" ")
        .hundredths(1205);
    assert_eq!(l.as_bytes(), b"sysmon: 0 4294967295 0.05 12.05");
    let mut l = Line::new();
    for _ in 0..20 {
        l.push(b"0123456789");
    }
    assert_eq!(l.as_bytes().len(), 64);
}

/// A fake system: files whose contents change at each read, a button that follows a script
/// (one value per read), a clock, and a SIGTERM at a set time.
#[derive(Default)]
struct Fake {
    stat: VecDeque<Vec<u8>>,
    meminfo: Vec<u8>,
    loadavg: Vec<u8>,
    button: VecDeque<Option<bool>>,
    leds: u32,
    broken_led: Option<u32>,
    now_ms: u32,
    term_at: Option<u32>,
    out: Vec<String>,
    /// When something else turns led2 off
    meddle_at_ms: Option<u32>,
    /// The LEDs at each read of /proc/uptime
    leds_at_sample: Vec<u32>,
}

impl Sys for Fake {
    fn read_file(&mut self, path: &'static [u8], buf: &mut [u8]) -> Option<usize> {
        let text = match path {
            b"/proc/uptime\0" => {
                self.leds_at_sample.push(self.leds);
                self.stat.pop_front()?
            }
            b"/proc/meminfo\0" => self.meminfo.clone(),
            b"/proc/loadavg\0" => self.loadavg.clone(),
            _ => return None,
        };
        let n = text.len().min(buf.len());
        buf[..n].copy_from_slice(&text[..n]);
        Some(n)
    }
    fn led(&mut self, n: u32, on: bool) -> bool {
        if self.broken_led == Some(n) {
            return false;
        }
        self.leds = if on {
            self.leds | 1 << n
        } else {
            self.leds & !(1 << n)
        };
        true
    }
    fn button(&mut self) -> Option<bool> {
        self.button.pop_front().unwrap_or(Some(false))
    }
    fn sleep_ms(&mut self, ms: u32) {
        self.now_ms += ms;
        if self.meddle_at_ms.is_some_and(|t| self.now_ms >= t) {
            self.meddle_at_ms = None;
            self.leds &= !(1 << FIRST_LED);
        }
    }
    fn stopping(&mut self) -> bool {
        self.term_at.is_some_and(|t| self.now_ms >= t)
    }
    fn say(&mut self, line: &[u8]) {
        self.out.push(String::from_utf8(line.to_vec()).unwrap());
    }
}

/// /proc/uptime after busy and idle hundredths of a second
fn stat(busy: u64, idle: u64) -> Vec<u8> {
    let up = busy + idle;
    format!(
        "{}.{:02} {}.{:02}\n",
        up / 100,
        up % 100,
        idle / 100,
        idle % 100
    )
    .into_bytes()
}

#[test]
fn cpu_bar_follows_the_load_and_count_stops_it() {
    let mut f = Fake {
        stat: [stat(0, 0), stat(30, 70), stat(30, 170), stat(130, 170)].into(),
        ..Fake::default()
    };
    f.leds = 0b111111; // the triggers' LEDs and a bar left lit
    let o = Options {
        count: 3,
        verbose: true,
        ..Options::default()
    };
    assert_eq!(run(&mut f, o), 0);
    assert_eq!(
        f.out,
        [
            "sysmon: mode cpu",
            "sysmon: cpu 30% bar 2",
            "sysmon: cpu 0% bar 0",
            "sysmon: cpu 100% bar 4",
            "sysmon: stopped"
        ]
    );
    assert_eq!(f.now_ms, 3000);
    // Its own LEDs off at the end; led0 and led1 are the triggers', never touched
    assert_eq!(f.leds, 0b000011);
}

#[test]
fn the_button_steps_through_the_modes_on_presses_only() {
    let mut f = Fake {
        stat: [stat(0, 0), stat(0, 0), stat(50, 50)].into(),
        meminfo: MEMINFO.to_vec(),
        loadavg: b"1.25 0 0 1/2 3\n".to_vec(),
        // At start; then each 200 ms: held for two reads (one press), released, pressed again
        button: [
            Some(false),
            Some(true),
            Some(true),
            Some(false),
            Some(false),
            Some(false),
            Some(true),
        ]
        .into(),
        ..Fake::default()
    };
    let o = Options {
        count: 3,
        verbose: true,
        ..Options::default()
    };
    assert_eq!(run(&mut f, o), 0);
    assert_eq!(
        f.out,
        [
            "sysmon: mode cpu",
            "sysmon: mode mem",
            "sysmon: mem 37% bar 2",
            "sysmon: mode load",
            "sysmon: load 1.25 bar 3",
            "sysmon: load 1.25 bar 3",
            "sysmon: stopped"
        ]
    );
}

#[test]
fn back_to_cpu_takes_a_new_first_sample() {
    let mut f = Fake {
        stat: [stat(20, 20), stat(40, 20)].into(),
        loadavg: b"0.00 0 0\n".to_vec(),
        button: [Some(false), Some(true)].into(),
        ..Fake::default()
    };
    let o = Options {
        mode: Mode::Load,
        count: 1,
        verbose: true,
        ..Options::default()
    };
    run(&mut f, o);
    // Load -> Cpu at the first read: sample (20, 20) first, so the value is (40-20)/(60-40)
    assert_eq!(f.out[1..3], ["sysmon: mode cpu", "sysmon: cpu 100% bar 4"]);
}

#[test]
fn sigterm_ends_it_dark_with_status_0() {
    let mut f = Fake {
        stat: [stat(0, 0), stat(100, 0)].into(),
        term_at: Some(1200),
        ..Fake::default()
    };
    assert_eq!(run(&mut f, Options::default()), 0);
    assert_eq!(f.out, ["sysmon: mode cpu", "sysmon: stopped"]);
    assert_eq!(f.leds, 0);
    // Stopped at the first read after the signal, not at the interval's end
    assert_eq!(f.now_ms, 1200);
}

#[test]
fn without_a_button_it_says_so_and_runs() {
    let mut f = Fake {
        meminfo: MEMINFO.to_vec(),
        button: [None, None].into(),
        ..Fake::default()
    };
    let o = Options {
        mode: Mode::Mem,
        interval_ms: 300,
        button_ms: 1000,
        count: 1,
        ..Options::default()
    };
    assert_eq!(run(&mut f, o), 0);
    assert_eq!(
        f.out,
        ["sysmon: no button", "sysmon: mode mem", "sysmon: stopped"]
    );
    assert_eq!(f.leds, 0);
    assert_eq!(f.now_ms, 300);
}

#[test]
fn an_unreadable_file_shows_nothing() {
    let mut f = Fake::default();
    let o = Options {
        count: 1,
        verbose: true,
        ..Options::default()
    };
    run(&mut f, o);
    assert_eq!(f.out[1], "sysmon: cpu ? bar 0");
}

#[test]
fn leds_it_cannot_write_end_it_with_status_1() {
    let mut f = Fake {
        broken_led: Some(3),
        ..Fake::default()
    };
    f.leds = 0b001000;
    assert_eq!(run(&mut f, Options::default()), 1);
    assert_eq!(f.out, ["sysmon: cannot write /sys/class/leds/led2-5"]);
    let mut f = Fake {
        stat: [stat(0, 0), stat(100, 0)].into(),
        broken_led: Some(3),
        ..Fake::default()
    };
    assert_eq!(run(&mut f, Options::default()), 1);
    assert_eq!(
        f.out.last().unwrap(),
        "sysmon: cannot write /sys/class/leds/led2-5"
    );
}

#[test]
fn leds_another_process_changed_are_written_again_at_the_refresh() {
    // Full load all along; at 1500 ms something else turns led2 off (a second sysmon ending,
    // the shell's echo)
    let stat_seq = (0..=REFRESH_INTERVALS + 1)
        .map(|k| stat(100 * u64::from(k), 0))
        .collect();
    let mut f = Fake {
        stat: stat_seq,
        meddle_at_ms: Some(1500),
        ..Fake::default()
    };
    run(
        &mut f,
        Options {
            count: REFRESH_INTERVALS + 1,
            ..Options::default()
        },
    );
    // At each sample (before that interval's write): dark, dark, then the bar without led2 up
    // to the refresh, which writes all four at the end of interval REFRESH_INTERVALS, then the
    // bar whole
    let mut want = vec![0, 0];
    want.extend([0b111000].repeat(REFRESH_INTERVALS as usize - 1));
    want.push(0b111100);
    assert_eq!(f.leds_at_sample, want);
}

#[test]
fn only_the_leds_that_change_are_written() {
    struct Count(Fake, u32);
    impl Sys for Count {
        fn read_file(&mut self, path: &'static [u8], buf: &mut [u8]) -> Option<usize> {
            self.0.read_file(path, buf)
        }
        fn led(&mut self, n: u32, on: bool) -> bool {
            self.1 += 1;
            self.0.led(n, on)
        }
        fn button(&mut self) -> Option<bool> {
            self.0.button()
        }
        fn sleep_ms(&mut self, ms: u32) {
            self.0.sleep_ms(ms)
        }
        fn stopping(&mut self) -> bool {
            self.0.stopping()
        }
        fn say(&mut self, line: &[u8]) {
            self.0.say(line)
        }
    }
    // 50% busy all along: bar 2 after the first interval, then unchanged for three
    let stat_seq = (0..=4).map(|k| stat(50 * k, 50 * k)).collect();
    let mut c = Count(
        Fake {
            stat: stat_seq,
            ..Fake::default()
        },
        0,
    );
    run(
        &mut c,
        Options {
            count: 4,
            ..Options::default()
        },
    );
    // 4 at the start (all, dark), 2 for the bar, none for three intervals, 4 at the end (all)
    assert_eq!(c.1, 4 + 2 + 4);
}
