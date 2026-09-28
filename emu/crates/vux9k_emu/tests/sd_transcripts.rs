// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Replays sim/sd_transcripts/*.txt (recorded from the RTL tests' Python card, see
//! sim/emu/test_sd_transcripts.py) through `SdCard`: every MISO byte, the final
//! sectors, the command log, the violation count and the power-up clocks must match.

use std::path::PathBuf;

use vux9k_emu::sdcard::SdCard;

fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
        .collect()
}

fn replay(path: &PathBuf) {
    let name = path.file_name().unwrap().to_string_lossy().into_owned();
    let text = std::fs::read_to_string(path).unwrap();
    let mut card: Option<SdCard> = None;
    let mut nbytes = 0usize;
    for (n, line) in text.lines().enumerate() {
        let at = format!("{name}:{}", n + 1);
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.is_empty() || f[0].starts_with('#') {
            continue;
        }
        if f[0] == "card" {
            let mut c = SdCard::new(Vec::new());
            for opt in &f[1..] {
                match opt.split_once('=') {
                    Some(("sdhc", v)) => c.sdhc = v == "1",
                    Some(("strict", v)) => c.strict = v == "1",
                    Some(("bad", v)) => {
                        c.faults.bad_sectors = v.split(',').map(|x| x.parse().unwrap()).collect()
                    }
                    Some(("mute", v)) => {
                        c.faults.mute_cmds = v.split(',').map(|x| x.parse().unwrap()).collect()
                    }
                    None if *opt == "never_ready" => c.faults.never_ready = true,
                    None if *opt == "read_error" => c.faults.read_error = true,
                    None if *opt == "write_reject" => c.faults.write_reject = true,
                    _ => panic!("{at}: unknown card option {opt}"),
                }
            }
            card = Some(c);
            continue;
        }
        let c = card
            .as_mut()
            .unwrap_or_else(|| panic!("{at}: no card line"));
        match f[0] {
            "sector" => c.set_sector(f[1].parse().unwrap(), &hex(f[2])),
            "cs" => c.set_cs(f[1] == "1"),
            "x" => {
                let sel = f[1] == "1";
                let (mosi, miso) = (hex(f[2]), hex(f[3]));
                for (i, (&o, &want)) in mosi.iter().zip(&miso).enumerate() {
                    let got = c.exchange(o, sel);
                    assert_eq!(
                        got, want,
                        "{at}: byte {i} of the run (MOSI {o:02x}): MISO {got:02x}, Python card {want:02x}"
                    );
                }
                nbytes += mosi.len();
            }
            "expect-sector" => {
                let lba: u32 = f[1].parse().unwrap();
                assert_eq!(c.sector(lba).to_vec(), hex(f[2]), "{at}: sector {lba}");
            }
            "expect-commands" => {
                let want: Vec<(u8, u32)> = f
                    .get(1)
                    .map_or("", |v| v)
                    .split(',')
                    .filter(|s| !s.is_empty())
                    .map(|s| {
                        let (cmd, arg) = s.split_once(':').unwrap();
                        (cmd.parse().unwrap(), u32::from_str_radix(arg, 16).unwrap())
                    })
                    .collect();
                assert_eq!(c.commands, want, "{at}");
            }
            "expect-violations" => {
                assert_eq!(
                    c.violations.len(),
                    f[1].parse::<usize>().unwrap(),
                    "{at}: {:?}",
                    c.violations
                )
            }
            "expect-idle-clocks" => {
                assert_eq!(c.idle_clocks(), f[1].parse::<u64>().unwrap(), "{at}")
            }
            other => panic!("{at}: unknown record {other}"),
        }
    }
    assert!(nbytes > 0, "{name}: no bytes");
}

#[test]
fn rust_card_matches_every_python_transcript() {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../sim/sd_transcripts");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("{}: {e}", dir.display()))
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    assert!(files.len() >= 4, "transcripts missing in {}", dir.display());
    for f in &files {
        replay(f);
    }
}
