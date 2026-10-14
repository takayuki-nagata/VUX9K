// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Long names and paths for a file manager (feature `fat-full`): directories listed with
//! the long names their entries carry (read only: what fw_common writes has an 8.3 name),
//! and paths resolved from a working directory, through `.` and `..`, by 8.3 or long name.
//! Kept apart from `dir`'s lookups, which the first stage links: none of this is in it.

use super::dir::{checksum, short_name, DirEntry, ATTR_LFN, ATTR_VOLUME, DELETED};
use super::{le16, BlockDevice, FatError, Volume};

/// The longest long name `list` passes: 20 entries of 13 characters (VFAT stops at 255).
pub const LONG_NAME_MAX: usize = 260;
const PARTS: u8 = 20;
/// Byte offsets of an entry's 13 UTF-16 characters.
const CHARS: [usize; 13] = [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30];
pub(crate) const DOT: [u8; 11] = *b".          ";
pub(crate) const DOTDOT: [u8; 11] = *b"..         ";

/// n * 13 by shifts (RV32I has no multiply).
fn times13(n: u8) -> usize {
    let n = n as usize;
    (n << 3) + (n << 2) + n
}

fn is_separator(c: &u8) -> bool {
    *c == b'/' || *c == b'\\'
}

/// `path` without separators at its end, cut before its last component (which keeps the
/// separator before it, so `/X` stays absolute): (head, last component).
fn split_last(path: &[u8]) -> (&[u8], &[u8]) {
    let end = path
        .iter()
        .rposition(|c| !is_separator(c))
        .map_or(0, |i| i + 1);
    let path = &path[..end];
    match path.iter().rposition(is_separator) {
        Some(i) => (&path[..=i], &path[i + 1..]),
        None => (&path[..0], path),
    }
}

impl<D: BlockDevice> Volume<D> {
    /// Call `f` with each entry of directory `dir` (`.` and `..` included; long-name,
    /// volume-label and deleted entries skipped) and its long name, until it returns true;
    /// returns that entry. The long name is the run of long-name entries before the entry,
    /// complete, in order and with the entry's checksum (else None), as ASCII: a UTF-16
    /// character outside 0x20..=0x7E reads as `?`.
    pub fn list(
        &mut self,
        dir: &DirEntry,
        f: &mut dyn FnMut(&DirEntry, Option<&[u8]>) -> bool,
    ) -> Result<Option<DirEntry>, FatError> {
        if !dir.is_dir() {
            return Err(FatError::WrongType);
        }
        let mut name = [0u8; LONG_NAME_MAX];
        // The run before the next entry as find_entry tracks it (where it starts, its
        // checksum), so that an entry listed here is removed with the same slots
        let mut run: Option<(u32, u8, u8)> = None;
        // The long name the run spells so far: its length and the part expected next (0
        // once complete); None when the run doesn't spell one
        let mut long: Option<(usize, u8)> = None;
        let mut found = None;
        let _ = self.walk_slots(dir.cluster, &mut |lba, i, e| {
            if e[0] == 0 {
                return true;
            }
            if e[0] == DELETED {
                run = None;
                long = None;
                return false;
            }
            if e[11] == ATTR_LFN {
                let part = e[0] & 0x1F;
                if e[0] & 0x40 != 0 || run.is_none_or(|r| r.2 != e[13]) {
                    run = Some((lba, i, e[13]));
                    // A name starts at its last part (bit 6), the longest it can be
                    long = (e[0] & 0x40 != 0 && (1..=PARTS).contains(&part))
                        .then_some((times13(part), part));
                }
                long = long
                    .filter(|l| part != 0 && l.1 == part)
                    .map(|(mut len, _)| {
                        let base = times13(part - 1);
                        for (k, &at) in CHARS.iter().enumerate() {
                            let c = le16(e, at);
                            if c == 0 {
                                // The end (then 0xFFFF padding)
                                len = len.min(base + k);
                            }
                            name[base + k] = if (0x20..=0x7E).contains(&c) {
                                c as u8
                            } else {
                                b'?'
                            };
                        }
                        (len, part - 1)
                    });
                return false;
            }
            let r = run.take().filter(|r| r.2 == checksum(&e[..11]));
            let l = long.take();
            if e[11] & ATTR_VOLUME != 0 {
                return false;
            }
            let entry = DirEntry::parse(e, lba, i, r.map(|r| (r.0, r.1)));
            let long_name = match (r, l) {
                (Some(_), Some((len, 0))) if len > 0 => Some(&name[..len]),
                _ => None,
            };
            if f(&entry, long_name) {
                found = Some(entry);
                return true;
            }
            false
        })?;
        Ok(found)
    }

    /// Follow `path` from directory `start` (from the root when it begins with `/` or
    /// `\`; either separator). `.` stays, `..` goes up (the root's is the root), and other
    /// components match an entry's 8.3 name, else its long name (ASCII case ignored).
    pub fn resolve(&mut self, start: &DirEntry, path: &[u8]) -> Result<DirEntry, FatError> {
        let mut at = match path.first() {
            Some(c) if is_separator(c) => self.root(),
            _ => *start,
        };
        for part in path.split(is_separator) {
            if part.is_empty() {
                continue;
            }
            if !at.is_dir() {
                return Err(FatError::WrongType);
            }
            at = match part {
                b"." => at,
                b".." => self.parent(&at)?,
                _ => self.lookup(&at, part)?,
            };
        }
        Ok(at)
    }

    /// Where `path` (from `start`, as `resolve` takes it) would be made: its directory
    /// and its last component as an 8.3 name (`BadName` if it isn't a valid one: new
    /// entries get no long name). Separators at the end are ignored.
    pub fn resolve_parent(
        &mut self,
        start: &DirEntry,
        path: &[u8],
    ) -> Result<(DirEntry, [u8; 11]), FatError> {
        let (head, last) = split_last(path);
        let name = short_name(last).ok_or(FatError::BadName)?;
        Ok((self.resolve_dir(start, head)?, name))
    }

    /// The entry `path` names (from `start`, as `resolve` takes it) and the directory it
    /// is in, for removing or moving it: `BadName` when the last component is `.`, `..`
    /// or missing (no entry to change). Separators at the end are ignored.
    pub fn locate(
        &mut self,
        start: &DirEntry,
        path: &[u8],
    ) -> Result<(DirEntry, DirEntry), FatError> {
        let (head, last) = split_last(path);
        if matches!(last, b"" | b"." | b"..") {
            return Err(FatError::BadName);
        }
        let dir = self.resolve_dir(start, head)?;
        let entry = self.lookup(&dir, last)?;
        Ok((dir, entry))
    }

    /// `resolve`, for a directory.
    fn resolve_dir(&mut self, start: &DirEntry, path: &[u8]) -> Result<DirEntry, FatError> {
        let dir = self.resolve(start, path)?;
        if !dir.is_dir() {
            return Err(FatError::WrongType);
        }
        Ok(dir)
    }

    /// The entry named `part` in `dir`: by 8.3 name, else by long name.
    fn lookup(&mut self, dir: &DirEntry, part: &[u8]) -> Result<DirEntry, FatError> {
        if let Some(short) = short_name(part) {
            if let Some(e) = self.find(dir, &short)? {
                return Ok(e);
            }
        }
        self.list(dir, &mut |_, long| {
            long.is_some_and(|l| l.eq_ignore_ascii_case(part))
        })?
        .ok_or(FatError::NotFound)
    }

    /// The directory `dir` is in, as its entry there (the root for the root): `dir`'s
    /// `..` names the parent's cluster, the parent's `..` the directory that holds the
    /// parent's entry.
    fn parent(&mut self, dir: &DirEntry) -> Result<DirEntry, FatError> {
        let Some(up) = self.dot_dot(dir)? else {
            return Ok(self.root());
        };
        let holder = self.dot_dot(&up)?.unwrap_or(self.root());
        self.find_entry(&holder, &mut |e| {
            e.is_dir() && e.cluster == up.cluster && e.name[0] != b'.'
        })?
        .ok_or(FatError::NotFound)
    }

    /// `dir`'s `..` entry, or None when it leads to the root (cluster 0, or the root's
    /// cluster as some tools write it) or `dir` is the root.
    fn dot_dot(&mut self, dir: &DirEntry) -> Result<Option<DirEntry>, FatError> {
        if dir.cluster == self.root {
            return Ok(None);
        }
        let up = self.find(dir, &DOTDOT)?.ok_or(FatError::NotFound)?;
        Ok((up.cluster != 0 && up.cluster != self.root).then_some(up))
    }
}
