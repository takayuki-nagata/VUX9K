// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! `vux9k-emu`: run the VUX9K emulator.
//!
//! By default the SoC powers up like the board: I-RAM and D-RAM preloaded from
//! build/firmware/firmware.hex and firmware_d0-3.hex (`--firmware DIR`), so the
//! Resident Loader and Boot Manager start, with an SD card from `--sd`.
//! `--load`/`--hex` put a program at address 0 instead (over the preload, or alone
//! with `--no-firmware`).
//!
//! The UART is connected to stdout (batch), the terminal (`--stdio`), or a TCP
//! client (`--tcp PORT`, e.g. `vux_tool.py --port socket://localhost:PORT`).
//! Interactive modes run at the board's speed (27 MHz, `--speed`), so the firmware's
//! timeouts behave as on hardware; batch mode runs as fast as it can.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::ExitCode;
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::time::{Duration, Instant};

use vux9k_emu::sdcard::SdCard;
use vux9k_emu::{hexfile, Profile, Soc, Stop};

const USAGE: &str = "\
usage: vux9k-emu [options]

  --profile real|extended|isa-test   memory profile (default real; extended is NOT REAL HARDWARE)
  --firmware DIR      preload firmware.hex + firmware_d0-3.hex from DIR (default build/firmware)
  --no-firmware       start with empty memories
  --load FILE         raw binary into I-RAM at address 0
  --hex FILE          $readmemh words into I-RAM from word 0 (e.g. build/hack/firmware.hex)
  --sd IMAGE          raw SD image (sector n at byte 512 n); changes are kept in memory
  --sd-write-back     write the SD image back to IMAGE on exit
  --sdsc              the card is SDSC (byte addressing) instead of SDHC
  --no-card           empty SD socket
  --stdio             UART <-> this terminal (input is sent line by line)
  --tcp PORT          UART <-> one TCP client on localhost:PORT
  --speed X           emulated/real time ratio in interactive modes (default 1, 0 = unlimited)
  --cycles N          stop after N clock cycles (default: unlimited, batch 100000000)
  --until TEXT        stop successfully once the UART output contains TEXT
  --trace FILE        write one line per executed instruction to FILE (- = stderr)
";

struct Opts {
    profile: Profile,
    firmware: Option<String>,
    load: Option<String>,
    hex: Option<String>,
    sd: Option<String>,
    sd_write_back: bool,
    sdsc: bool,
    card: bool,
    stdio: bool,
    tcp: Option<u16>,
    speed: f64,
    cycles: Option<u64>,
    until: Option<Vec<u8>>,
    trace: Option<String>,
}

fn parse_args() -> Result<Opts, String> {
    let mut o = Opts {
        profile: Profile::Real,
        firmware: Some("build/firmware".into()),
        load: None,
        hex: None,
        sd: None,
        sd_write_back: false,
        sdsc: false,
        card: true,
        stdio: false,
        tcp: None,
        speed: 1.0,
        cycles: None,
        until: None,
        trace: None,
    };
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut val = |name: &str| args.next().ok_or(format!("{name} needs a value"));
        match arg.as_str() {
            "--profile" => {
                o.profile = match val("--profile")?.as_str() {
                    "real" => Profile::Real,
                    "extended" => Profile::EXTENDED,
                    "isa-test" => Profile::IsaTest,
                    p => return Err(format!("unknown profile {p:?}")),
                }
            }
            "--firmware" => o.firmware = Some(val("--firmware")?),
            "--no-firmware" => o.firmware = None,
            "--load" => o.load = Some(val("--load")?),
            "--hex" => o.hex = Some(val("--hex")?),
            "--sd" => o.sd = Some(val("--sd")?),
            "--sd-write-back" => o.sd_write_back = true,
            "--sdsc" => o.sdsc = true,
            "--no-card" => o.card = false,
            "--stdio" => o.stdio = true,
            "--tcp" => o.tcp = Some(val("--tcp")?.parse().map_err(|e| format!("--tcp: {e}"))?),
            "--speed" => {
                o.speed = val("--speed")?
                    .parse()
                    .map_err(|e| format!("--speed: {e}"))?
            }
            "--cycles" => {
                o.cycles = Some(
                    val("--cycles")?
                        .parse()
                        .map_err(|e| format!("--cycles: {e}"))?,
                )
            }
            "--until" => {
                let t = val("--until")?;
                if t.is_empty() {
                    return Err("--until needs a non-empty text".into());
                }
                o.until = Some(t.into_bytes())
            }
            "--trace" => o.trace = Some(val("--trace")?),
            "-h" | "--help" => return Err(String::new()),
            a => return Err(format!("unknown argument {a:?}")),
        }
    }
    if o.stdio && o.tcp.is_some() {
        return Err("--stdio and --tcp are exclusive".into());
    }
    if o.sd_write_back && o.sd.is_none() {
        return Err("--sd-write-back needs --sd".into());
    }
    Ok(o)
}

fn read_text(path: &str) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))
}

fn setup(o: &Opts) -> Result<Soc, String> {
    let mut soc = Soc::new(o.profile);
    if let Some(dir) = &o.firmware {
        let iram = read_text(&format!("{dir}/firmware.hex"))?;
        let lanes: Vec<String> = (0..4)
            .map(|i| read_text(&format!("{dir}/firmware_d{i}.hex")))
            .collect::<Result<_, _>>()?;
        soc.load_readmemh(&iram, [&lanes[0], &lanes[1], &lanes[2], &lanes[3]])?;
    }
    if let Some(path) = &o.load {
        let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
        soc.load_iram(0, &bytes);
    }
    if let Some(path) = &o.hex {
        soc.load_iram_words(0, &hexfile::parse(&read_text(path)?)?);
    }
    if o.card {
        let image = match &o.sd {
            Some(path) => std::fs::read(path).map_err(|e| format!("{path}: {e}"))?,
            None => Vec::new(),
        };
        let mut card = SdCard::new(image);
        card.sdhc = !o.sdsc;
        soc.periph.sd.card = Some(card);
    }
    Ok(soc)
}

/// Bytes from the host side of the UART.
enum Input {
    Data(Vec<u8>),
    Closed,
}

fn spawn_reader(mut r: impl Read + Send + 'static) -> Receiver<Input> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match r.read(&mut buf) {
                Ok(0) | Err(_) => {
                    let _ = tx.send(Input::Closed);
                    return;
                }
                Ok(n) => {
                    if tx.send(Input::Data(buf[..n].to_vec())).is_err() {
                        return;
                    }
                }
            }
        }
    });
    rx
}

fn trace_line(r: &vux9k_emu::Retire) -> String {
    let mut s = format!(
        "{:>10} {} {:08x} {:08x}",
        r.cycle,
        if r.riscv { "rv" } else { "hk" },
        r.pc,
        r.instr
    );
    if let Some(m) = r.trap {
        s += &format!(" trap={m:#x}");
    }
    for (i, v) in r.rd.iter().chain(r.rd2.iter()) {
        s += &format!(" x{i}={v:08x}");
    }
    if let Some((a, d, be)) = r.store {
        s += &format!(" [{a:08x}]={d:08x}/{be:x}");
    }
    if let Some(t) = r.branch_taken {
        s += if t { " taken" } else { " not-taken" };
    }
    s
}

fn main() -> ExitCode {
    let o = match parse_args() {
        Ok(o) => o,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("vux9k-emu: {e}");
            }
            eprint!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    let mut soc = match setup(&o) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("vux9k-emu: {e}");
            return ExitCode::from(1);
        }
    };
    eprintln!("vux9k-emu: profile {}", o.profile.describe());

    // Host side of the UART
    let interactive = o.stdio || o.tcp.is_some();
    let (input, mut output): (Option<Receiver<Input>>, Box<dyn Write>) = if let Some(port) = o.tcp {
        let listener = match TcpListener::bind(("127.0.0.1", port)) {
            Ok(l) => l,
            Err(e) => {
                eprintln!("vux9k-emu: tcp port {port}: {e}");
                return ExitCode::from(1);
            }
        };
        eprintln!("vux9k-emu: waiting for a UART client on socket://localhost:{port}");
        let stream: TcpStream = match listener.accept() {
            Ok((s, _)) => s,
            Err(e) => {
                eprintln!("vux9k-emu: accept: {e}");
                return ExitCode::from(1);
            }
        };
        let _ = stream.set_nodelay(true);
        let reader = stream.try_clone().expect("clone TCP stream");
        (Some(spawn_reader(reader)), Box::new(stream))
    } else if o.stdio {
        (
            Some(spawn_reader(std::io::stdin())),
            Box::new(std::io::stdout()),
        )
    } else {
        (None, Box::new(std::io::stdout()))
    };
    let mut trace: Option<Box<dyn Write>> = match o.trace.as_deref() {
        None => None,
        Some("-") => Some(Box::new(std::io::stderr())),
        Some(p) => match std::fs::File::create(p) {
            Ok(f) => Some(Box::new(std::io::BufWriter::new(f))),
            Err(e) => {
                eprintln!("vux9k-emu: {p}: {e}");
                return ExitCode::from(1);
            }
        },
    };

    let budget = o
        .cycles
        .unwrap_or(if interactive { u64::MAX } else { 100_000_000 });
    let speed = if interactive { o.speed } else { 0.0 };
    // Slices of 1 ms of board time between host I/O and pacing
    const SLICE: u64 = 27_000;
    let start = Instant::now();
    let mut sent = 0usize;
    let mut found = false;
    let mut stop = Stop::Budget;
    let mut host_closed = false;
    'run: while soc.cycle < budget {
        if let Some(rx) = &input {
            loop {
                match rx.try_recv() {
                    Ok(Input::Data(d)) => soc.uart_send(&d),
                    Ok(Input::Closed) | Err(TryRecvError::Disconnected) => {
                        host_closed = true;
                        break;
                    }
                    Err(TryRecvError::Empty) => break,
                }
            }
        }
        let end = soc.cycle.saturating_add(SLICE).min(budget);
        while soc.cycle < end {
            let r = soc.step();
            if let Some(t) = trace.as_mut() {
                let _ = writeln!(t, "{}", trace_line(&r));
            }
            if let Some(code) = soc.tohost {
                stop = Stop::ToHost(code);
                break 'run;
            }
        }
        let got = soc.uart_received();
        if got.len() > sent {
            let new = &got[sent..];
            if output.write_all(new).and_then(|_| output.flush()).is_err() {
                host_closed = true;
            }
            if let Some(needle) = &o.until {
                let from = sent.saturating_sub(needle.len());
                if got[from..].windows(needle.len()).any(|w| w == &needle[..]) {
                    found = true;
                    break;
                }
            }
            sent = got.len();
        }
        // Stop once the host is gone and everything it sent has been received
        if host_closed && soc.cycle >= soc.periph.uart.host_send_done() {
            break;
        }
        if speed > 0.0 {
            let due = Duration::from_secs_f64(soc.cycle as f64 / 27_000_000.0 / speed);
            if let Some(wait) = due.checked_sub(start.elapsed()) {
                std::thread::sleep(wait);
            }
        }
    }
    if let Some(t) = trace.as_mut() {
        let _ = t.flush();
    }
    let _ = output.flush();

    if o.sd_write_back {
        if let (Some(path), Some(card)) = (&o.sd, &soc.periph.sd.card) {
            if let Err(e) = std::fs::write(path, card.image()) {
                eprintln!("vux9k-emu: writing back {path}: {e}");
                return ExitCode::from(1);
            }
        }
    }
    if let Some(card) = &soc.periph.sd.card {
        for v in &card.violations {
            eprintln!("vux9k-emu: SD protocol violation: {v}");
        }
    }
    eprintln!(
        "vux9k-emu: stopped at cycle {} ({} instructions, pc={:#010x}): {}",
        soc.cycle,
        soc.steps,
        soc.pc,
        match stop {
            Stop::ToHost(v) => format!("tohost {v:#x}"),
            Stop::Budget if found => "--until text received".into(),
            Stop::Budget if host_closed => "host closed the UART".into(),
            Stop::Budget => "cycle budget".into(),
        }
    );
    match stop {
        Stop::ToHost(1) => ExitCode::SUCCESS,
        Stop::ToHost(_) => ExitCode::from(1),
        Stop::Budget if o.until.is_some() && !found => ExitCode::from(1),
        Stop::Budget => ExitCode::SUCCESS,
    }
}
