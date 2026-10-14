// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage's command line, the part without MMIO: a line typed byte by byte
//! (`LineBuf`, with backspace), split into a command and its arguments (`Args`), and
//! the working directory's path as `pwd` shows it (`Path`).

/// The longest line (longer input is dropped, not echoed).
pub const LINE_MAX: usize = 128;
/// The most arguments a command takes (`mv SRC DST`, `install -u`); `Args::extra` tells
/// when a line has more.
pub const ARGS_MAX: usize = 2;
const BS: u8 = 0x08;
const DEL: u8 = 0x7F;

/// What a byte did to the line, i.e. what to echo.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Key {
    /// Added to the line: echo it.
    Char(u8),
    /// The last byte was removed: echo backspace, space, backspace.
    Erase,
    /// Carriage return: the line is complete (`LineBuf::line`, then `clear`).
    Enter,
    /// Nothing changed (a control byte, a backspace on an empty line, a full line).
    Ignored,
}

/// A line being typed. `feed` is on the per-byte path of code that runs from the
/// PSRAM (a fetch costs ~35 cycles): it has no loop and is inlined.
pub struct LineBuf {
    buf: [u8; LINE_MAX],
    len: usize,
}

impl Default for LineBuf {
    fn default() -> Self {
        Self::new()
    }
}

impl LineBuf {
    pub const fn new() -> Self {
        LineBuf {
            buf: [0; LINE_MAX],
            len: 0,
        }
    }

    /// Take one received byte: printable ASCII is added, backspace or DEL removes the last
    /// byte, carriage return ends the line; line feed (after a carriage return) and other
    /// control bytes are ignored.
    #[inline(always)]
    pub fn feed(&mut self, b: u8) -> Key {
        match b {
            b'\r' => Key::Enter,
            BS | DEL if self.len > 0 => {
                self.len -= 1;
                Key::Erase
            }
            0x20..=0x7E if self.len < LINE_MAX => {
                self.buf[self.len] = b;
                self.len += 1;
                Key::Char(b)
            }
            _ => Key::Ignored,
        }
    }

    /// The line so far.
    pub fn line(&self) -> &[u8] {
        &self.buf[..self.len]
    }

    pub fn clear(&mut self) {
        self.len = 0;
    }
}

/// A line split at spaces (and tabs): the command, up to `ARGS_MAX` arguments.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Args<'a> {
    /// The first word (empty for a blank line).
    pub cmd: &'a [u8],
    args: [&'a [u8]; ARGS_MAX],
    count: usize,
    /// Words past `ARGS_MAX` arguments.
    pub extra: bool,
}

impl<'a> Args<'a> {
    pub fn parse(line: &'a [u8]) -> Args<'a> {
        let mut out = Args {
            cmd: &line[..0],
            args: [&line[..0]; ARGS_MAX],
            count: 0,
            extra: false,
        };
        let mut words = line
            .split(|&c| c == b' ' || c == b'\t')
            .filter(|w| !w.is_empty());
        if let Some(cmd) = words.next() {
            out.cmd = cmd;
        }
        for w in words {
            if out.count == ARGS_MAX {
                out.extra = true;
                break;
            }
            out.args[out.count] = w;
            out.count += 1;
        }
        out
    }

    /// The arguments after the command.
    pub fn args(&self) -> &[&'a [u8]] {
        &self.args[..self.count]
    }

    /// Exactly `n` arguments (no more): Some(them).
    pub fn exactly(&self, n: usize) -> Option<&[&'a [u8]]> {
        (self.count == n && !self.extra).then(|| self.args())
    }
}

/// The longest path `Path` holds.
pub const PATH_MAX: usize = 96;

fn is_separator(c: &u8) -> bool {
    *c == b'/' || *c == b'\\'
}

/// The working directory's path as typed to get there, from the root: `/` and the
/// components separated by `/`, with `.` and `..` applied (as `fat::Volume::resolve`
/// applies them; the root's `..` is the root).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Path {
    buf: [u8; PATH_MAX],
    len: usize,
}

impl Default for Path {
    fn default() -> Self {
        Self::root()
    }
}

impl Path {
    pub const fn root() -> Self {
        let mut buf = [0; PATH_MAX];
        buf[0] = b'/';
        Path { buf, len: 1 }
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.buf[..self.len]
    }

    /// The path after `cd arg` from here (`arg` from the root when it starts with a
    /// separator; either separator); None if it doesn't fit `PATH_MAX`.
    pub fn join(&self, arg: &[u8]) -> Option<Path> {
        let mut p = match arg.first() {
            Some(c) if is_separator(c) => Path::root(),
            _ => *self,
        };
        for part in arg.split(is_separator) {
            match part {
                b"" | b"." => {}
                b".." => {
                    // Back to the separator before the last component (the root stays)
                    let cut = p.as_bytes().iter().rposition(|&c| c == b'/').unwrap_or(0);
                    p.len = cut.max(1);
                }
                _ => {
                    let sep = (p.len > 1) as usize;
                    if p.len + sep + part.len() > PATH_MAX {
                        return None;
                    }
                    if sep == 1 {
                        p.buf[p.len] = b'/';
                    }
                    p.buf[p.len + sep..p.len + sep + part.len()].copy_from_slice(part);
                    p.len += sep + part.len();
                }
            }
        }
        Some(p)
    }
}
