// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The prompt: lines typed with echo and backspace (fw_common::shell::LineBuf), run on
//! Enter. The second stage runs from the PSRAM (a fetch that misses the instruction cache
//! waits ~35 cycles; a hit doesn't), so the per-byte loop here has no calls besides the
//! inlined UART accesses and `feed`.

use fw_common::shell::{Args, Key, LineBuf, Path};
use fw_drivers::uart::Uart;

use crate::{cmds, launch};

pub const PROMPT: &str = "s2> ";

/// What the commands share: the working directory (None: the root, also before a card
/// is mounted) and its path, and the first stage's version (a2).
pub struct Ctx {
    pub cwd: Option<fw_common::fat::DirEntry>,
    pub path: Path,
    pub s1_version: u32,
}

const HELP: &str = "\
Commands (paths with / or \\, . and .., 8.3 or long names):
  help             this list
  ls [PATH]        list a directory (or show a file)
  cd PATH | pwd    change | show the current directory
  cat FILE         print a file
  hexdump FILE     print a file in hex
  rm FILE          remove a file
  mkdir DIR        make a directory
  rmdir DIR        remove an empty directory
  mv SRC DST       move or rename a file (DST may be a directory)
  put NAME         receive a file over the UART (transfer v2) as NAME
  run FILE         start a VUX9 v4 application (RV32 or Hack, in the PSRAM)
  info             card, free space, versions
  install [-u]     receive a second stage and install it as \\VUX9K\\STAGE2.BIN
                   (-u: unchecked, for tests)
  reset            soft reset (back to the first stage)
";

pub fn run(s1_version: u32) -> ! {
    let mut ctx = Ctx {
        cwd: None,
        path: Path::root(),
        s1_version,
    };
    let mut line = LineBuf::new();
    Uart::print_str(PROMPT);
    loop {
        let Some(b) = Uart::read_byte() else {
            continue;
        };
        match line.feed(b) {
            Key::Char(c) => Uart::write_byte(c),
            Key::Erase => {
                Uart::write_byte(0x08);
                Uart::write_byte(b' ');
                Uart::write_byte(0x08);
            }
            Key::Ignored => {}
            Key::Enter => {
                Uart::write_byte(b'\n');
                execute(&mut ctx, line.line());
                line.clear();
                Uart::print_str(PROMPT);
            }
        }
    }
}

/// The commands that need the card: name, what runs, the arguments it takes (from, to),
/// how it is used.
#[derive(Clone, Copy)]
enum Cmd {
    Ls,
    Cd,
    Cat,
    Hexdump,
    Rm,
    Mkdir,
    Rmdir,
    Mv,
    Put,
    Run,
    Info,
}

const CARD_CMDS: [(&[u8], Cmd, usize, usize, &str); 11] = [
    (b"ls", Cmd::Ls, 0, 1, "ls [PATH]"),
    (b"cd", Cmd::Cd, 1, 1, "cd PATH"),
    (b"cat", Cmd::Cat, 1, 1, "cat FILE"),
    (b"hexdump", Cmd::Hexdump, 1, 1, "hexdump FILE"),
    (b"rm", Cmd::Rm, 1, 1, "rm FILE"),
    (b"mkdir", Cmd::Mkdir, 1, 1, "mkdir DIR"),
    (b"rmdir", Cmd::Rmdir, 1, 1, "rmdir DIR"),
    (b"mv", Cmd::Mv, 2, 2, "mv SRC DST"),
    (b"put", Cmd::Put, 1, 1, "put NAME"),
    (b"run", Cmd::Run, 1, 1, "run FILE"),
    (b"info", Cmd::Info, 0, 0, "info"),
];

/// Wrong arguments: say how the command is used.
fn usage(how: &str) {
    Uart::print_str("[ERR] usage: ");
    Uart::print_str(how);
    Uart::print_str("\n");
}

fn execute(ctx: &mut Ctx, line: &[u8]) {
    let a = Args::parse(line);
    let args = a.args();
    match a.cmd {
        b"" => return,
        b"help" => return Uart::print_str(HELP),
        b"pwd" => {
            Uart.put_bytes(ctx.path.as_bytes());
            return Uart::print_str("\n");
        }
        b"install" => {
            return match (args, a.extra) {
                ([], _) => launch::install(false),
                ([b"-u"], false) => launch::install(true),
                _ => usage("install [-u]"),
            }
        }
        b"reset" => launch::soft_reset(),
        _ => {}
    }
    let Some(&(_, cmd, min, max, how)) = CARD_CMDS.iter().find(|c| c.0 == a.cmd) else {
        return Uart::print_str("[ERR] unknown command (help lists them)\n");
    };
    if a.extra || args.len() < min || args.len() > max {
        return usage(how);
    }
    let path = args.first().copied().unwrap_or(b".");
    let r = cmds::mount(ctx).and_then(|mut card| match cmd {
        Cmd::Ls => cmds::ls(&mut card, path),
        Cmd::Cd => cmds::cd(ctx, &mut card, path),
        Cmd::Cat => cmds::show(&mut card, path, false),
        Cmd::Hexdump => cmds::show(&mut card, path, true),
        Cmd::Rm => cmds::rm(&mut card, path),
        Cmd::Mkdir => cmds::mkdir(&mut card, path),
        Cmd::Rmdir => cmds::rmdir(&mut card, path),
        Cmd::Mv => cmds::mv(&mut card, path, args[1]),
        Cmd::Put => cmds::put(&mut card, path),
        Cmd::Run => launch::run(ctx, &mut card, path),
        Cmd::Info => cmds::info(ctx, &mut card),
    });
    if let Err(e) = r {
        cmds::print_error(e);
    }
}

/// Bytes to the UART, as they are.
pub trait PutBytes {
    fn put_bytes(&mut self, b: &[u8]);
}

impl PutBytes for Uart {
    fn put_bytes(&mut self, b: &[u8]) {
        for &c in b {
            Uart::write_byte(c);
        }
    }
}
