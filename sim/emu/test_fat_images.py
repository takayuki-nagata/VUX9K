# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""FAT32 images from the real tools and from vux_tool, for fw_common::fat's host tests.

firmware/fw_common/tests/fat_images/ holds card images made by mkfs.fat and mtools
(`mkfs_*`) and by tools/vux_tool.py's build_fat32_image (`vuxtool_*`), stored sparse
(`<name>.sparse`: only the sectors that aren't all zero) so that the host tests need neither
tool, each with a `<name>.files` list (`path size crc32` per file, by the name fw_common
opens it with: 8.3, or for mkfs_lfn long names) of what fw_common must read from it. This
test rebuilds them and checks they are what the tools make today (they are byte-for-byte
reproducible: fixed volume ID, SOURCE_DATE_EPOCH, file times), that fsck.fat finds nothing
wrong with them, that mtools reads vux_tool's files back, and that they stay clean after
fw_common writes to them (the `fat_host` example: install, remove, mkdir, rmdir, move, and
its free-cluster count against fsck.fat's).
VUX9_VECTORS_UPDATE=1 rewrites them.

The checks with dosfstools and mtools are skipped without them, and fail instead when
VUX9K_REQUIRE_FAT_TOOLS=1 (make test-fat, and so CI). mtools before 4.0.49 (CI's Ubuntu 22.04
has 4.0.32) takes a FAT32 volume of fewer than 65525 clusters for FAT16 and fails on it, so
with those the small images are checked with fsck.fat and fat_host only, mtools reads a
full-size FAT32 image from vux_tool instead, and the mkfs/mtools images aren't rebuilt.
"""

import binascii
import os
import re
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
needs_tools = pytest.mark.skipif(bool(MISSING), reason=f"needs dosfstools and mtools ({', '.join(MISSING)} missing)")

SECTOR = 512
PART_LBA = 2048  # where SD cards start their first partition
EPOCH = 315532800  # 1980-01-01 00:00 UTC, the first FAT date
TOOL_ENV = dict(os.environ, TZ="UTC", SOURCE_DATE_EPOCH=str(EPOCH), MTOOLS_SKIP_CHECK="1")
SPARSE_MAGIC = b"VUXSPRS1"
# The mtools the mkfs_* images were made with; rebuilding them needs it (other versions write
# other bytes, and older ones can't handle small FAT32 volumes at all)
FIXTURE_MTOOLS = (4, 0, 49)
# Partition sectors for a FAT32 volume of at least 65525 one-sector clusters, which every
# mtools version reads as FAT32
FULL_SIZE_SECTORS = 70_000


def mtools_version():
    """(major, minor, patch) of the installed mtools, or None."""
    if shutil.which("mtools") is None:
        return None
    out = subprocess.run(["mtools", "--version"], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


MTOOLS = mtools_version()
# Whether mtools reads FAT32 volumes below 65525 clusters (the fixtures) as FAT32
MTOOLS_SMALL_FAT32 = MTOOLS is not None and MTOOLS >= FIXTURE_MTOOLS


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


def from_sparse(raw):
    assert raw[:8] == SPARSE_MAGIC
    (total,) = struct.unpack_from("<I", raw, 8)
    img = bytearray(total * SECTOR)
    for at in range(12, len(raw), 4 + SECTOR):
        (lba,) = struct.unpack_from("<I", raw, at)
        img[lba * SECTOR : (lba + 1) * SECTOR] = raw[at + 4 : at + 4 + SECTOR]
    return bytes(img)


def run_tool(*cmd, **kw):
    return subprocess.run(cmd, env=TOOL_ENV, capture_output=True, timeout=60, **kw)


def fsck(path, tmp_dir, fsinfo_unknown=False):
    """fsck.fat -n on the partition of card image `path`: nothing but its banner, the
    small-FAT32 warning and the summary; with fsinfo_unknown, also its note that FSInfo's
    free count is unknown (fw_common sets it so instead of keeping it right). Returns the
    free clusters by the summary."""
    part = os.path.join(tmp_dir, "partition.img")
    with open(path, "rb") as f, open(part, "wb") as out:
        f.seek(PART_LBA * SECTOR)
        out.write(f.read())
    r = run_tool("fsck.fat", "-n", part, text=True)
    os.unlink(part)
    out = (r.stdout + r.stderr).splitlines()
    expected: tuple[str, ...] = ("fsck.fat ", "Warning: Filesystem is FAT32 according to", "  but has only")
    expected += ("  This may lead to",)
    if fsinfo_unknown:
        expected += ("Free cluster summary uninitialized",)
    extra = [ln for ln in out if ln.strip() and not ln.startswith(expected) and not ln.startswith(part + ":")]
    assert r.returncode == 0 and not extra, "\n".join(out)
    m = re.search(r"(\d+)/(\d+) clusters$", r.stdout, re.M)
    assert m, "\n".join(out)
    return int(m.group(2)) - int(m.group(1))


def mtype(path, name):
    r = run_tool("mtype", "-i", f"{path}@@{PART_LBA * SECTOR}", f"::{name}")
    assert r.returncode == 0, r.stderr
    return r.stdout


def mdir(path, name):
    r = run_tool("mdir", "-i", f"{path}@@{PART_LBA * SECTOR}", f"::{name}", text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


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
        r = run_tool(*cmd)
        assert r.returncode == 0, r.stderr

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


def build_mkfs_lfn(card, tmp):
    """512-byte clusters, two FATs, no second stage (an install makes \\VUX9K\\): long names
    for directories and files (one of four entries, 52 characters and no end mark; one of
    exactly 13; one not in ASCII), a lowercase 8.3 name (no long name), a directory in a
    long-named one, and a removed long-named file. Listed by the names fw_common resolves
    (`Volume::resolve`, feature fat-full): long names, with `?` for what isn't ASCII."""
    card.mkdir("Projects Folder")
    card.put("Projects Folder/A Very Long File Name That Needs Several Entries.txt", data(700, 4), tmp)
    card.mkdir("Projects Folder/sub")
    card.put("Projects Folder/sub/x.txt", b"nested\n", tmp)
    card.put("Deleted Long Name.txt", b"gone\n", tmp)
    card.put("\u00dcn\u00efc\u00f6d\u00e9 file.txt", b"unicode\n", tmp, short="?n?c?d? file.txt")
    card.delete("Deleted Long Name.txt")
    card.put("ABCDEFGHIJ.KL", b"thirteen\n", tmp)
    card.put("Mixed Case.Txt", b"mixed\n", tmp)
    card.put("lower.txt", b"lower\n", tmp)
    card.put("README.TXT", b"hello\n", tmp)


def vuxtool_files(cluster_sectors):
    if cluster_sectors == 1:
        files = {
            "VUX9K/STAGE2.BIN": stage2(1500, 2),
            "VUX9K/STAGE2.OLD": stage2(700, 1, seed=5),
            "README.TXT": b"hello\n",
            "EMPTY.DAT": b"",
            "APPS/HELLO.BIN": data(3000, 7),
            "VUX9K/SUB/X.TXT": b"nested\n",
        }
        # A directory over several clusters
        files.update({f"VUX9K/F{i:02}.DAT": bytes([i]) for i in range(20)})
        return files
    return {"VUX9K/STAGE2.BIN": stage2(10000, 3), "APPS/BIG.BIN": data(20000, 9)}


# name -> (partition sectors, sectors per cluster, FATs, builder | None for vux_tool's)
IMAGES = {
    "mkfs_s1": (8192, 1, 2, build_mkfs_s1),
    "mkfs_s8_1fat": (8192, 8, 1, build_mkfs_s8_1fat),
    "mkfs_lfn": (8192, 1, 2, build_mkfs_lfn),
    "vuxtool_s1": (8192, 1, 2, None),
    "vuxtool_s8": (16384, 8, 2, None),
}


def make_image(name, tmp):
    """(image bytes, {path: content}) as built today."""
    part_sectors, cluster_sectors, fats, build = IMAGES[name]
    if build is None:
        files = vuxtool_files(cluster_sectors)
        return vux_tool.build_fat32_image(files, part_sectors=part_sectors, cluster_sectors=cluster_sectors), files
    card = Card(os.path.join(tmp, "card.img"), part_sectors, cluster_sectors, fats)
    build(card, tmp)
    with open(card.path, "rb") as f:
        return f.read(), card.files


def manifest(files):
    rows = [f"{p} {len(c)} {binascii.crc32(c) & 0xFFFFFFFF:08x}" for p, c in sorted(files.items())]
    return "\n".join(rows) + "\n"


def check_fixture(name, img, files):
    sparse_path = os.path.join(IMAGE_DIR, f"{name}.sparse")
    files_path = os.path.join(IMAGE_DIR, f"{name}.files")
    if UPDATE:
        os.makedirs(IMAGE_DIR, exist_ok=True)
        with open(sparse_path, "wb") as f:
            f.write(to_sparse(img))
        with open(files_path, "w") as f:
            f.write(manifest(files))
    with open(sparse_path, "rb") as f:
        assert from_sparse(f.read()) == img, f"{name}.sparse differs from what is built today (VUX9_VECTORS_UPDATE=1)"
    with open(files_path) as f:
        assert f.read() == manifest(files)


@pytest.mark.parametrize("name", sorted(n for n in IMAGES if IMAGES[n][3] is None))
def test_vux_tool_images(name, tmp_path):
    img, files = make_image(name, str(tmp_path))
    check_fixture(name, img, files)


@needs_tools
@pytest.mark.skipif(
    MTOOLS != FIXTURE_MTOOLS and not UPDATE,
    reason=f"the mkfs_* images were made with mtools {FIXTURE_MTOOLS}, this is {MTOOLS}",
)
@pytest.mark.parametrize("name", sorted(n for n in IMAGES if IMAGES[n][3] is not None))
def test_tool_images(name, tmp_path):
    assert MTOOLS == FIXTURE_MTOOLS, f"rebuild the mkfs_* images with mtools {FIXTURE_MTOOLS}, not {MTOOLS}"
    img, files = make_image(name, str(tmp_path))
    check_fixture(name, img, files)


@needs_tools
@pytest.mark.parametrize("name", sorted(IMAGES))
def test_fixtures_pass_fsck(name, tmp_path):
    path = write_fixture(name, tmp_path)
    fsck(path, str(tmp_path))
    if MTOOLS_SMALL_FAT32 and IMAGES[name][3] is None:
        _, files = make_image(name, str(tmp_path))
        for p, content in files.items():
            assert mtype(path, p) == content, p


@needs_tools
def test_full_size_vux_tool_image_passes_fsck_and_mtools(tmp_path):
    files = vuxtool_files(1)
    path = str(tmp_path / "card.img")
    with open(path, "wb") as f:
        f.write(vux_tool.build_fat32_image(files, part_sectors=FULL_SIZE_SECTORS))
    fsck(path, str(tmp_path))
    for p, content in files.items():
        assert mtype(path, p) == content, p


def write_fixture(name, tmp_path):
    """The committed fixture `name` as an image file; its path."""
    with open(os.path.join(IMAGE_DIR, f"{name}.sparse"), "rb") as f:
        img = from_sparse(f.read())
    path = str(tmp_path / "card.img")
    with open(path, "wb") as f:
        f.write(img)
    return path


def test_no_stale_images():
    names = {n.rsplit(".", 1)[0] for n in os.listdir(IMAGE_DIR)}
    assert names == set(IMAGES)


def test_short_names_and_bad_trees():
    assert vux_tool.fat_short_name("stage2.bin") == b"STAGE2  BIN"
    assert vux_tool.fat_short_name("VUX9K") == b"VUX9K      "
    for bad in ("", ".BIN", "A.", "123456789", "A.1234", "A B", "A*", "ß.BIN", "Ä"):
        with pytest.raises(ValueError):
            vux_tool.fat_short_name(bad)
    with pytest.raises(ValueError, match="in the way"):
        vux_tool.build_fat32_image({"A.BIN": b"x", "A.BIN/B.BIN": b"y"})
    with pytest.raises(ValueError, match="twice"):
        vux_tool.build_fat32_image({"a.bin": b"x", "A.BIN": b"y"})
    with pytest.raises(ValueError, match="fit"):
        vux_tool.build_fat32_image({"BIG.BIN": bytes(5 * 1024 * 1024)})
    with pytest.raises(ValueError, match="power of two"):
        vux_tool.build_fat32_image({}, cluster_sectors=3)


def test_mkfat_cli(tmp_path):
    src = tmp_path / "s2.bin"
    src.write_bytes(stage2(100, 1))
    out = tmp_path / "card.img"
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "vux_tool.py"), "mkfat", str(out), "--file",
         f"VUX9K/STAGE2.BIN={src}"],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.read_bytes() == vux_tool.build_fat32_image({"VUX9K/STAGE2.BIN": stage2(100, 1)})
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "vux_tool.py"), "mkfat", str(out), "--file", "NOEQUALS"],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert r.returncode == 2 and "PATH=FILE" in r.stdout


# ----- What fw_common writes, checked by the tools ---------------------------------


@pytest.fixture(scope="module")
def fat_host():
    """The fat_host example (fw_common::fat with fat-full, on an image file), built."""
    host = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    triple = next(ln.split()[1] for ln in host.splitlines() if ln.startswith("host:"))
    fw = os.path.join(REPO_ROOT, "firmware")
    subprocess.run(
        ["cargo", "build", "-q", "-p", "fw_common", "--example", "fat_host", "--features", "fat-full",
         "--target", triple],
        cwd=fw, check=True, timeout=600,
    )  # fmt: skip
    return os.path.join(fw, "target", triple, "debug", "examples", "fat_host")


def fat_host_run(binary, *args):
    r = subprocess.run([binary, *args], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode()
    return r.stdout


@needs_tools
@pytest.mark.parametrize("name", [*sorted(IMAGES), "full_size"])
def test_fw_common_writes_pass_fsck(name, tmp_path, fat_host):
    """Installs and removals by fw_common, then fsck.fat; the files read back with mtools
    where it can read the image (full_size: a vux_tool image any mtools reads), else with
    fat_host."""
    if name == "full_size":
        path = str(tmp_path / "card.img")
        with open(path, "wb") as f:
            f.write(vux_tool.build_fat32_image(vuxtool_files(1), part_sectors=FULL_SIZE_SECTORS))
    else:
        path = write_fixture(name, tmp_path)
    use_mtools = name == "full_size" or MTOOLS_SMALL_FAT32

    def read(p):
        return mtype(path, p) if use_mtools else fat_host_run(fat_host, path, "cat", p)

    # mkfs_lfn has no second stage: the first install makes \\VUX9K\\
    has_stage2 = "VUX9K" in ls(fat_host, path, "")
    previous = read("VUX9K/STAGE2.BIN") if has_stage2 else None
    if has_stage2:
        assert fat_host_run(fat_host, path, "cat", "VUX9K/STAGE2.BIN") == previous
    # Two installs: the first new image over 128 clusters (the chain crosses FAT sectors)
    for version, size in ((7, 70_000), (8, 900)):
        new = stage2(size, version, seed=version)
        (tmp_path / "new.bin").write_bytes(new)
        fat_host_run(fat_host, path, "install", str(tmp_path / "new.bin"))
        fsck(path, str(tmp_path), fsinfo_unknown=True)
        assert read("VUX9K/STAGE2.BIN") == new
        if previous is None:
            assert "STAGE2.OLD" not in ls(fat_host, path, "VUX9K")
        else:
            assert read("VUX9K/STAGE2.OLD") == previous
        assert fat_host_run(fat_host, path, "boot") == f"BIN {version}\n".encode()
        previous = new
    # Removing files, long-named ones with their long-name entries (fsck.fat reports
    # long-name entries left without their 8.3 entry)
    for short in [n for n in ls(fat_host, path, "VUX9K") if "~" in n] + ["STAGE2.OLD"]:
        fat_host_run(fat_host, path, "rm", f"VUX9K/{short}")
        fsck(path, str(tmp_path), fsinfo_unknown=True)
    assert not [n for n in ls(fat_host, path, "VUX9K") if "~" in n]
    if use_mtools:
        assert "Long File Name" not in mdir(path, "VUX9K")


def ls(binary, image, path):
    """The 8.3 names fat_host lists in `path`."""
    return [ln.split("\t")[0] for ln in fat_host_run(binary, image, "ls", path).decode().splitlines()]


def fat_host_fails(binary, *args):
    """fat_host's error message for a command that must fail."""
    r = subprocess.run([binary, *args], capture_output=True, timeout=60)
    assert r.returncode != 0
    return r.stderr.decode()


@needs_tools
@pytest.mark.parametrize("name", [*sorted(IMAGES), "full_size"])
def test_fw_common_directory_changes_pass_fsck(name, tmp_path, fat_host):
    """mkdir, moves across and within directories, rmdir and the free-cluster count by
    fw_common (feature fat-full), each followed by fsck.fat (and read back with mtools
    where it can read the image); a long-named file moved loses its long name cleanly."""
    if name == "full_size":
        path = str(tmp_path / "card.img")
        with open(path, "wb") as f:
            f.write(vux_tool.build_fat32_image(vuxtool_files(1), part_sectors=FULL_SIZE_SECTORS))
    else:
        path = write_fixture(name, tmp_path)
    use_mtools = name == "full_size" or MTOOLS_SMALL_FAT32

    def step(*args):
        fat_host_run(fat_host, path, *args)
        free = fsck(path, str(tmp_path), fsinfo_unknown=True)
        assert fat_host_run(fat_host, path, "free") == f"{free}\n".encode()

    def read(p):
        return mtype(path, p) if use_mtools else fat_host_run(fat_host, path, "cat", p)

    free = fsck(path, str(tmp_path))
    assert fat_host_run(fat_host, path, "free") == f"{free}\n".encode()
    content = data(3000, 11)
    (tmp_path / "a.bin").write_bytes(content)
    step("mkdir", "TEST")
    step("mkdir", "test/SUB")
    assert ls(fat_host, path, "TEST/SUB") == [".", ".."]
    if use_mtools:
        assert "SUB" in mdir(path, "TEST")
    step("put", "TEST/A.BIN", str(tmp_path / "a.bin"))
    step("mv", "TEST/A.BIN", "TEST/SUB/B.BIN")
    assert ls(fat_host, path, "TEST") == [".", "..", "SUB"]
    step("mv", "TEST/SUB/B.BIN", "/TEST/SUB/C.BIN")
    assert read("TEST/SUB/C.BIN") == content
    assert "NotEmpty" in fat_host_fails(fat_host, path, "rmdir", "TEST")
    assert "WrongType" in fat_host_fails(fat_host, path, "mv", "TEST/SUB", "X")
    # A long-named file moved to the root: its long-name entries go with the old entry
    listing = fat_host_run(fat_host, path, "ls", "VUX9K" if name != "mkfs_lfn" else "Projects Folder").decode()
    long_named = [ln.split("\t") for ln in listing.splitlines() if "\t" in ln]
    if long_named:
        short, long = long_named[0]
        where = "VUX9K" if name != "mkfs_lfn" else "Projects Folder"
        before = fat_host_run(fat_host, path, "cat", f"{where}/{long}")
        step("mv", f"{where}/{long}", "MOVED.TXT")
        assert read("MOVED.TXT") == before
        assert short not in ls(fat_host, path, where)
        if use_mtools:
            assert long not in mdir(path, where)
    step("rm", "TEST/SUB/C.BIN")
    step("rmdir", "TEST/SUB/")
    step("rmdir", "TEST")
    assert "TEST" not in ls(fat_host, path, "")
