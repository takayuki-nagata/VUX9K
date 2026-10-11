# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""FAT32 images from the real tools, for fw_common::fat's host tests.

firmware/fw_common/tests/fat_images/ holds card images made by mkfs.fat and mtools, stored
sparse (`<name>.sparse`: only the sectors that aren't all zero) so that the host tests need
neither tool, each with a `<name>.files` list (`path size crc32` per file, by 8.3 name) of what fw_common
must read from it. This test rebuilds them and checks they are what the tools make today
(they are byte-for-byte reproducible: fixed volume ID, SOURCE_DATE_EPOCH, file times) and
that fsck.fat finds nothing wrong. VUX9_VECTORS_UPDATE=1 rewrites them.

Needs dosfstools and mtools: skipped without them, failed instead when
VUX9K_REQUIRE_FAT_TOOLS=1 (make test-fat, and so CI).
"""

import binascii
import os
import shutil
import struct
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

IMAGE_DIR = os.path.join(REPO_ROOT, "firmware", "fw_common", "tests", "fat_images")
UPDATE = os.environ.get("VUX9_VECTORS_UPDATE") == "1"
TOOLS = ("mkfs.fat", "fsck.fat", "mcopy", "mmd", "mdel")
MISSING = [t for t in TOOLS if shutil.which(t) is None]
if MISSING and os.environ.get("VUX9K_REQUIRE_FAT_TOOLS") == "1":
    raise RuntimeError(f"VUX9K_REQUIRE_FAT_TOOLS=1 but {', '.join(MISSING)} not found (dosfstools, mtools)")
pytestmark = pytest.mark.skipif(bool(MISSING), reason=f"needs dosfstools and mtools ({', '.join(MISSING)} missing)")

SECTOR = 512
PART_LBA = 2048  # where SD cards start their first partition
EPOCH = 315532800  # 1980-01-01 00:00 UTC, the first FAT date
TOOL_ENV = dict(os.environ, TZ="UTC", SOURCE_DATE_EPOCH=str(EPOCH), MTOOLS_SKIP_CHECK="1")
SPARSE_MAGIC = b"VUXSPRS1"


def data(n, seed=0):
    return bytes((i * 7 + 3 + seed) & 0xFF for i in range(n))


def to_sparse(img):
    """`VUXSPRS1`, total sectors (u32 LE), then (LBA u32 LE, 512 bytes) per non-zero sector."""
    out = bytearray(SPARSE_MAGIC + struct.pack("<I", len(img) // SECTOR))
    for lba in range(len(img) // SECTOR):
        sector = img[lba * SECTOR : (lba + 1) * SECTOR]
        if any(sector):
            out += struct.pack("<I", lba) + sector
    return bytes(out)


def mbr(part_lba, part_sectors, ptype=0x0C):
    """Sector 0 with one partition entry (LBA addressing; CHS fields saturated)."""
    sector = bytearray(SECTOR)
    sector[446:462] = struct.pack("<B3sB3sII", 0, b"\xfe\xff\xff", ptype, b"\xfe\xff\xff", part_lba, part_sectors)
    sector[510:512] = b"\x55\xaa"
    return bytes(sector)


class Card:
    """A card image file: an MBR, then a FAT32 partition from mkfs.fat at PART_LBA."""

    def __init__(self, path, part_sectors, cluster_sectors, fats):
        self.path = path
        self.files = {}
        with open(path, "wb") as f:
            f.write(mbr(PART_LBA, part_sectors))
            f.truncate((PART_LBA + part_sectors) * SECTOR)
        self.run(
            "mkfs.fat", "-F", "32", "-s", str(cluster_sectors), "-f", str(fats), "--invariant", "-n", "VUX9K",
            "--offset", str(PART_LBA), path, str(part_sectors // 2),
        )  # fmt: skip

    def run(self, *cmd):
        subprocess.run(cmd, env=TOOL_ENV, check=True, capture_output=True, timeout=60)

    def mtools(self, cmd, *args):
        self.run(cmd, "-i", f"{self.path}@@{PART_LBA * SECTOR}", *args)

    def mkdir(self, path):
        self.mtools("mmd", f"::{path}")

    def put(self, path, content, tmp_dir, short=None):
        """Copy content to path. The manifest lists it under `short`, the 8.3 alias mtools
        gives a long name (what fw_common, which reads 8.3 names only, opens)."""
        src = os.path.join(tmp_dir, os.path.basename(path))
        with open(src, "wb") as f:
            f.write(content)
        os.utime(src, (EPOCH, EPOCH))
        self.mtools("mcopy", "-m", src, f"::{path}")
        os.unlink(src)
        self.files[short or path] = content

    def delete(self, path):
        self.mtools("mdel", f"::{path}")
        del self.files[path]

    def forget_next_free(self):
        """FSInfo's next-free hint to 'unknown', so the next file takes the first free
        clusters: freed holes get reused and the file comes out fragmented."""
        with open(self.path, "r+b") as f:
            f.seek(PART_LBA * SECTOR + 48)
            (fsinfo,) = struct.unpack("<H", f.read(2))
            f.seek((PART_LBA + fsinfo) * SECTOR + 492)
            f.write(struct.pack("<I", 0xFFFFFFFF))

    def fsck(self, tmp_dir):
        part = os.path.join(tmp_dir, "partition.img")
        with open(self.path, "rb") as f, open(part, "wb") as out:
            f.seek(PART_LBA * SECTOR)
            out.write(f.read())
        r = subprocess.run(["fsck.fat", "-n", part], env=TOOL_ENV, capture_output=True, text=True, timeout=60)
        os.unlink(part)
        assert r.returncode == 0, r.stdout + r.stderr
        # Only the small-FAT32 warning (fewer clusters than 65525 is still FAT32 by its BPB)
        lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
        assert not [ln for ln in lines if "error" in ln.lower() or "differ" in ln.lower()], r.stdout

    def manifest(self):
        rows = [f"{p} {len(c)} {binascii.crc32(c) & 0xFFFFFFFF:08x}" for p, c in sorted(self.files.items())]
        return "\n".join(rows) + "\n"


def stage2(n, version, seed=0):
    raw, _ = vux_tool.build_vux9_v4_image(data(n, seed), name="Stage2", version=version, load_addr=0x80700000)
    return raw


def build_mkfs_s1(card, tmp):
    """512-byte clusters, two FATs: the second stage and its backup, a directory longer than
    a cluster, long names (one across the cluster boundary), a fragmented file, a nested
    directory, an empty file."""
    card.mkdir("VUX9K")
    card.put("A.BIN", data(2000, 1), tmp)
    card.put("B.BIN", data(600, 2), tmp)
    card.delete("A.BIN")
    card.forget_next_free()
    card.put("FRAG.BIN", data(5000, 3), tmp)
    card.put("readme.txt", b"hello\n", tmp)
    card.put("VUX9K/STAGE2.BIN", stage2(1500, 2), tmp)
    card.put("VUX9K/STAGE2.OLD", stage2(700, 1, seed=5), tmp)
    card.put("VUX9K/Long File Name.txt", b"lfn\n", tmp, short="VUX9K/LONGFI~1.TXT")
    for i in range(20):
        card.put(f"VUX9K/F{i:02}.DAT", bytes([i]), tmp)
    card.put("VUX9K/EMPTY.DAT", b"", tmp)
    card.mkdir("VUX9K/SUB")
    card.put("VUX9K/SUB/X.TXT", b"nested\n", tmp)
    # Three long-name entries in slots 29-31 and the 8.3 one in slot 32: the run crosses
    # from the directory's first cluster into its second
    card.put("VUX9K/A Rather Long File Name 01.dat", b"cross\n", tmp, short="VUX9K/ARATHE~1.DAT")


def build_mkfs_s8_1fat(card, tmp):
    """4 KB clusters, one FAT: a second stage over several clusters, and a long name whose
    entries cross a sector boundary inside the directory's cluster."""
    card.mkdir("VUX9K")
    card.put("VUX9K/STAGE2.BIN", stage2(10000, 3), tmp)
    for i in range(12):
        card.put(f"VUX9K/G{i:02}.DAT", bytes([i]), tmp)
    # ., .., STAGE2.BIN and G00-G11 fill slots 0-14; two long-name entries in 15 and 16
    card.put("VUX9K/Long File Name.txt", b"lfn\n", tmp, short="VUX9K/LONGFI~1.TXT")


IMAGES = {
    "mkfs_s1": (8192, 1, 2, build_mkfs_s1),
    "mkfs_s8_1fat": (8192, 8, 1, build_mkfs_s8_1fat),
}


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_images_match_the_tools(name, tmp_path):
    part_sectors, cluster_sectors, fats, build = IMAGES[name]
    card = Card(str(tmp_path / "card.img"), part_sectors, cluster_sectors, fats)
    build(card, str(tmp_path))
    card.fsck(str(tmp_path))
    with open(card.path, "rb") as f:
        sparse = to_sparse(f.read())
    manifest = card.manifest()
    sparse_path = os.path.join(IMAGE_DIR, f"{name}.sparse")
    files_path = os.path.join(IMAGE_DIR, f"{name}.files")
    if UPDATE:
        os.makedirs(IMAGE_DIR, exist_ok=True)
        with open(sparse_path, "wb") as f:
            f.write(sparse)
        with open(files_path, "w") as f:
            f.write(manifest)
    with open(sparse_path, "rb") as f:
        assert f.read() == sparse, f"{name}.sparse differs from what the tools make (VUX9_VECTORS_UPDATE=1)"
    with open(files_path) as f:
        assert f.read() == manifest


def test_no_stale_images():
    names = {n.rsplit(".", 1)[0] for n in os.listdir(IMAGE_DIR)}
    assert names == set(IMAGES)
