// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The writes an update of the second stage needs (feature `fat-write`): create a file in
//! an existing directory, rename and remove one. Every FAT copy gets each change, and
//! FSInfo's free count and next-free hint are marked unknown before the first change, so
//! nothing has to keep them right. Times are fixed at 1980-01-01 00:00 (no clock).
//!
//! Order for power loss: a new file's data and chain reach the card before its directory
//! entry, and a removed file's entry goes before its chain, so an interrupted change
//! leaves at most clusters no entry uses (which fsck reports, and nothing reads).

use super::dir::{DirEntry, ATTR_DIR, DELETED, ENTRY};
use super::{le32, read, BlockDevice, FatError, Volume, ENTRY_MASK, EOC_MIN, SECTOR};

const ATTR_ARCHIVE: u8 = 0x20;
/// 1980-01-01 as a FAT date.
const DATE_1980: u16 = (1 << 5) | 1;
const FSINFO_LEAD: u32 = 0x4161_5252;
const FSINFO_STRUCT: u32 = 0x6141_7272;

impl<D: BlockDevice> Volume<D> {
    fn write_sector(&mut self, lba: u32, which: Buf) -> Result<(), FatError> {
        let buf = match which {
            Buf::Data => &self.buf,
            Buf::Fat => &self.fat_buf,
        };
        if self.dev.write(lba, buf) {
            Ok(())
        } else {
            Err(FatError::Io(lba))
        }
    }

    /// Write the cached FAT sector to every FAT copy, if it changed.
    pub(crate) fn flush_fat(&mut self) -> Result<(), FatError> {
        if self.fat_dirty {
            for copy in 0..self.num_fats {
                // num_fats is 1 or 2: the second copy follows the first
                let base = if copy == 0 {
                    self.fat_lba
                } else {
                    self.fat_lba + self.fat_sectors
                };
                self.write_sector(base + self.fat_cached, Buf::Fat)?;
            }
            self.fat_dirty = false;
        }
        Ok(())
    }

    /// Set cluster `c`'s FAT entry (its top 4 bits are reserved and kept).
    fn set_fat(&mut self, c: u32, value: u32) -> Result<(), FatError> {
        self.load_fat(c >> 7)?;
        let at = ((c & 127) << 2) as usize;
        let old = le32(&self.fat_buf, at);
        self.fat_buf[at..at + 4].copy_from_slice(&((old & !ENTRY_MASK) | value).to_le_bytes());
        self.fat_dirty = true;
        Ok(())
    }

    /// Before the first change: FSInfo's free count and next-free hint to unknown (when
    /// the volume has a valid FSInfo sector).
    fn begin_change(&mut self) -> Result<(), FatError> {
        if self.fsinfo_done {
            return Ok(());
        }
        self.fsinfo_done = true;
        let lba = self.fsinfo_lba;
        if lba <= self.part_lba || lba >= self.fat_lba {
            return Ok(());
        }
        read(&mut self.dev, lba, &mut self.buf)?;
        if le32(&self.buf, 0) != FSINFO_LEAD || le32(&self.buf, 484) != FSINFO_STRUCT {
            return Ok(());
        }
        // Free count and next free: 0xFFFFFFFF, unknown
        self.buf[488..496].copy_from_slice(&[0xFF; 8]);
        self.write_sector(lba, Buf::Data)
    }

    /// A free cluster, marked end-of-chain and linked after `prev`.
    fn alloc_cluster(&mut self, prev: Option<u32>) -> Result<u32, FatError> {
        let mut c = self.next_free;
        for _ in 2..self.cluster_end {
            if c >= self.cluster_end {
                c = 2;
            }
            if self.fat_entry(c)? == 0 {
                self.set_fat(c, ENTRY_MASK)?;
                if let Some(p) = prev {
                    self.set_fat(p, c)?;
                }
                self.next_free = c + 1;
                return Ok(c);
            }
            c += 1;
        }
        Err(FatError::DiskFull)
    }

    /// Free a chain from `first` to its end. A looping chain comes back to a cluster
    /// this has freed already (entry 0, not a cluster), so the loop ends.
    fn free_chain(&mut self, first: u32) -> Result<(), FatError> {
        let mut c = first;
        loop {
            if !self.is_cluster(c) {
                return Err(FatError::BadChain(c));
            }
            let next = self.fat_entry(c)?;
            self.set_fat(c, 0)?;
            if next >= EOC_MIN {
                return self.flush_fat();
            }
            c = next;
        }
    }

    /// The entry slot after (lba, index) in the same directory.
    fn next_slot(&mut self, lba: u32, index: u8) -> Result<(u32, u8), FatError> {
        if (index as usize + 1) < SECTOR / ENTRY {
            return Ok((lba, index + 1));
        }
        let rel = lba - self.data_lba;
        if (rel + 1) & ((1 << self.cluster_shift) - 1) != 0 {
            return Ok((lba + 1, 0));
        }
        let c = (rel >> self.cluster_shift) + 2;
        let next = self.next_cluster(c)?.ok_or(FatError::BadChain(c))?;
        Ok((self.cluster_lba(next), 0))
    }

    /// Mark the slots from `entry`'s long-name run up to `entry` itself deleted, but leave
    /// `entry` when `keep` (a rename).
    fn delete_slots(&mut self, entry: &DirEntry, keep: bool) -> Result<(), FatError> {
        let (mut lba, mut index) = (entry.lfn_lba, entry.lfn_index);
        loop {
            let last = (lba, index) == (entry.lba, entry.index);
            if last && keep {
                return Ok(());
            }
            read(&mut self.dev, lba, &mut self.buf)?;
            self.buf[index as usize * ENTRY] = DELETED;
            self.write_sector(lba, Buf::Data)?;
            if last {
                return Ok(());
            }
            (lba, index) = self.next_slot(lba, index)?;
        }
    }

    /// A free entry slot in `dir`: the first deleted or never-used one, or the first of a
    /// zeroed cluster added to the directory.
    fn free_slot(&mut self, dir: &DirEntry) -> Result<(u32, u8), FatError> {
        let last =
            match self.walk_slots(dir.cluster, &mut |_, _, e| e[0] == 0 || e[0] == DELETED)? {
                Ok(slot) => return Ok(slot),
                Err(last) => last,
            };
        // Directory full: one more cluster, zeroed (all entries unused)
        let new = self.alloc_cluster(Some(last))?;
        self.flush_fat()?;
        self.buf = [0; SECTOR];
        let lba = self.cluster_lba(new);
        for s in 0..1u32 << self.cluster_shift {
            self.write_sector(lba + s, Buf::Data)?;
        }
        Ok((lba, 0))
    }

    /// Write `data` into newly allocated clusters; the first one (0 for no data). On
    /// failure the clusters taken so far are given back where the card allows.
    fn write_chain(&mut self, data: &[u8]) -> Result<u32, FatError> {
        let mut first = 0;
        let mut prev = None;
        let result = (|| {
            for cluster in data.chunks(SECTOR << self.cluster_shift) {
                let c = self.alloc_cluster(prev)?;
                if first == 0 {
                    first = c;
                }
                prev = Some(c);
                let lba = self.cluster_lba(c);
                for (s, chunk) in cluster.chunks(SECTOR).enumerate() {
                    self.buf[..chunk.len()].copy_from_slice(chunk);
                    self.buf[chunk.len()..].fill(0);
                    self.write_sector(lba + s as u32, Buf::Data)?;
                }
            }
            self.flush_fat()
        })();
        match result {
            Ok(()) => Ok(first),
            Err(e) => {
                if first != 0 {
                    let _ = self.free_chain(first);
                }
                Err(e)
            }
        }
    }

    /// Create the file `name` (8.3, as `to_short_name` makes it) in directory `dir` with
    /// `data`. The data and its chain are written before the entry.
    pub fn create(
        &mut self,
        dir: &DirEntry,
        name: &[u8; 11],
        data: &[u8],
    ) -> Result<DirEntry, FatError> {
        if self.find(dir, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        let first = self.write_chain(data)?;
        let (lba, index) = self.free_slot(dir)?;
        read(&mut self.dev, lba, &mut self.buf)?;
        let e = &mut self.buf[index as usize * ENTRY..(index as usize + 1) * ENTRY];
        e.fill(0);
        e[..11].copy_from_slice(name);
        e[11] = ATTR_ARCHIVE;
        for at in [16, 18, 24] {
            // creation, access and write dates
            e[at..at + 2].copy_from_slice(&DATE_1980.to_le_bytes());
        }
        e[20..22].copy_from_slice(&((first >> 16) as u16).to_le_bytes());
        e[26..28].copy_from_slice(&(first as u16).to_le_bytes());
        e[28..32].copy_from_slice(&(data.len() as u32).to_le_bytes());
        self.write_sector(lba, Buf::Data)?;
        Ok(DirEntry {
            name: *name,
            attr: ATTR_ARCHIVE,
            cluster: first,
            size: data.len() as u32,
            lba,
            index,
            lfn_lba: lba,
            lfn_index: index,
        })
    }

    /// Rename a file within its directory (`dir`, where `entry` was found) to `name`.
    /// A long name it had is removed.
    pub fn rename(
        &mut self,
        dir: &DirEntry,
        entry: &DirEntry,
        name: &[u8; 11],
    ) -> Result<(), FatError> {
        if entry.attr & ATTR_DIR != 0 {
            return Err(FatError::WrongType);
        }
        if self.find(dir, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        self.delete_slots(entry, true)?;
        read(&mut self.dev, entry.lba, &mut self.buf)?;
        let at = entry.index as usize * ENTRY;
        self.buf[at..at + 11].copy_from_slice(name);
        self.write_sector(entry.lba, Buf::Data)
    }

    /// Remove a file: its entry (and long name), then its clusters.
    pub fn remove(&mut self, entry: &DirEntry) -> Result<(), FatError> {
        if entry.attr & ATTR_DIR != 0 {
            return Err(FatError::WrongType);
        }
        self.begin_change()?;
        self.delete_slots(entry, false)?;
        if entry.cluster != 0 {
            self.free_chain(entry.cluster)?;
        }
        Ok(())
    }
}

/// Which buffer `write_sector` writes.
enum Buf {
    Data,
    Fat,
}
