# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The second stage (firmware/stage2) on the emulator: its line prompt (echo, backspace,
usage), the file commands on FAT32 cards (long names read, 8.3 names written, every error
they report), put/install over transfer v2 through tools/vux_tool.py, `run` of the two
targets of fw_common::launch (PSRAM, I-RAM Hack) and what it refuses (an RV32 file at 0
among them, with the first stage left intact in I-RAM), `info`, `reset` and the trap report.
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integration"))
from rv32_asm import Asm  # noqa: E402
from s1_env import (  # noqa: E402
    BANNER,
    BOOT_CYCLES,
    FAT_LBA,
    S1_PROMPT,
    S2_BANNER,
    S2_PROMPT,
    STAGE1_DIR,
    STAGE1_VERSION,
    STAGE2_VERSION,
    boot_s2,
    card,
    fixture,
    handoff,
    s2_cmd,
    stage2,
    start,
)
from vux9k import REPO_ROOT, UART_BIT, EmuClock, EmuSerial, ms, read_hex_words, vux_tool, wait_for  # noqa: E402

S2 = "VUX9K/STAGE2.BIN"
HACK_BIN = os.path.join(REPO_ROOT, "build", "hack", "firmware.bin")
HACK_DONE = b"ALL HACK C FIRMWARE TESTS PASSED (100%)!"
S1_WORDS = read_hex_words(os.path.join(STAGE1_DIR, "firmware.hex"))
# Run commands: the [RUN] line and the blank line after it
RUN_CYCLES = 20_000_000


def err(out: str) -> str:
    """The [ERR] line of a reply (without the tag), or fail."""
    lines = [ln for ln in out.splitlines() if ln.startswith("[ERR] ")]
    assert lines, out
    return lines[0][len("[ERR] ") :]


def ls_line(name: str, size, long: str | None = None) -> str:
    """One `ls` line as the second stage prints it."""
    col = "<DIR>" if size is None else str(size)
    return f"{name:<13}{col:>10}" + (f"  {long}" if long else "")


def hexdump(data: bytes) -> str:
    out = ""
    for i in range(0, len(data), 16):
        c = data[i : i + 16]
        out += f"{i:08X} " + "".join(f" {b:02X}" for b in c) + "   " * (16 - len(c))
        out += "  |" + "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in c) + "|\n"
    return out


def s1_intact(soc) -> bool:
    """I-RAM still holds the first stage word for word."""
    return all(soc.iram_word(4 * i) == w for i, w in enumerate(S1_WORDS))


def app(origin: int, text: str, tail=None) -> bytes:
    """RV32 code at origin printing text (each byte once the TX FIFO has room) and then
    stopping in a loop (or running `tail(asm)` instead)."""
    a = Asm(origin=origin)
    a.lui("t1", 0x40000)  # UART
    for i, ch in enumerate(text):
        a.label(f"w{i}")
        a.lw("t0", 4, "t1")
        a.andi("t0", "t0", 2)  # TX full
        a.bne("t0", "zero", f"w{i}")
        a.addi("t2", "zero", ord(ch))
        a.sb("t2", 0, "t1")
    if tail:
        tail(a)
    a.label("stop")
    a.jal("zero", "stop")
    words = a.assemble()
    return struct.pack(f"<{len(words)}I", *words)


def v4(payload: bytes, **kw) -> bytes:
    data, _ = vux_tool.build_vux9_v4_image(payload, name=kw.pop("name", "app"), **kw)
    return data


@pytest.fixture
def tool(monkeypatch):
    """(soc, EmuSerial) for vux_tool: the SoC with `image`, run to the second stage's
    prompt (booted from the card, or RAM-booted when the card has none)."""

    def make(image, ram_boot=False, **kw):
        soc = start(image, **kw)
        monkeypatch.setattr(vux_tool, "time", EmuClock(soc))
        ser = EmuSerial(soc, timeout=0.05)
        if ram_boot:
            vux_tool.enter_s1(ser, timeout=30.0)
            vux_tool.ram_boot(ser, stage2(), timeout=30.0)
        else:
            got, out = vux_tool.read_until(ser, (S2_PROMPT.decode(),), 10.0)
            assert got, out
        return soc, ser

    return make


# ----- the prompt ---------------------------------------------------------------------


def test_banner_handoff_and_help():
    soc, out = boot_s2()
    assert S2_BANNER in out and "Type help for the commands." in out
    assert handoff(out) == (1, FAT_LBA, STAGE1_VERSION)
    out = s2_cmd(soc, "help")
    for c in ["ls [PATH]", "cd PATH | pwd", "cat FILE", "hexdump FILE", "rm FILE", "mkdir DIR", "rmdir DIR",
              "mv SRC DST", "put NAME", "run FILE", "info", "install [-u]", "reset"]:  # fmt: skip
        assert f"  {c} " in out, (c, out)
    assert s2_cmd(soc, "") == ""
    assert s2_cmd(soc, "   ") == ""
    assert err(s2_cmd(soc, "frobnicate")) == "unknown command (help lists them)"


@pytest.mark.parametrize(
    "line, how",
    [
        ("ls A B", "ls [PATH]"),
        ("cd", "cd PATH"),
        ("cat", "cat FILE"),
        ("hexdump A B", "hexdump FILE"),
        ("rm", "rm FILE"),
        ("mkdir", "mkdir DIR"),
        ("rmdir A B", "rmdir DIR"),
        ("mv A", "mv SRC DST"),
        ("mv A B C", "mv SRC DST"),
        ("put", "put NAME"),
        ("run", "run FILE"),
        ("info X", "info"),
        ("install -x", "install [-u]"),
        ("install -u X", "install [-u]"),
    ],
)
def test_usage(line, how):
    soc, _ = boot_s2()
    assert err(s2_cmd(soc, line)) == f"usage: {how}"


def test_line_editing():
    soc, _ = boot_s2({"README.TXT": b"hi\n"})
    soc.run(50 * UART_BIT)
    mark = len(soc.uart_received())
    # Backspace and DEL erase (also on an empty line, where nothing is echoed); a line feed,
    # controls and non-ASCII bytes are ignored
    soc.uart_send(b"\x08cx\x08t\x7fat \x1b\xe9READ\nME.TXT\r")
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES, start=mark)
    assert out == "cx\x08 \x08t\x08 \x08at README.TXT\nhi\ns2> ", repr(out)
    # A full line (128) takes no more
    mark = len(soc.uart_received())
    soc.uart_send(b"x" * 140 + b"\r")
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES, start=mark)
    assert out.startswith("x" * 128 + "\n[ERR] unknown command"), out


# ----- files --------------------------------------------------------------------------

FILES = {
    "README.TXT": b"hi\n",
    "NOEOL.TXT": b"no line end",
    "EMPTY.DAT": b"",
    "VUX9K/APPS/HELLO.BIN": bytes(range(40)),
    "DOCS/A.TXT": b"a\n",
}


def test_ls():
    soc, _ = boot_s2(FILES)
    out = s2_cmd(soc, "ls")
    assert sorted(out.splitlines()) == [
        ls_line("DOCS", None),
        ls_line("EMPTY.DAT", 0),
        ls_line("NOEOL.TXT", 11),
        ls_line("README.TXT", 3),
        ls_line("VUX9K", None),
    ], out
    assert ls_line("STAGE2.BIN", len(stage2())) in s2_cmd(soc, "ls VUX9K")
    # `.` and `..` are not listed; a file lists itself
    assert s2_cmd(soc, "ls VUX9K/APPS") == ls_line("HELLO.BIN", 40) + "\n"
    assert s2_cmd(soc, "ls /vux9k/apps/hello.bin") == ls_line("HELLO.BIN", 40) + "\n"
    assert s2_cmd(soc, "ls \\VUX9K\\APPS\\..\\..\\DOCS") == ls_line("A.TXT", 2) + "\n"
    assert err(s2_cmd(soc, "ls NOPE")) == "not found"
    assert err(s2_cmd(soc, "ls README.TXT/X")) == "not a directory"
    assert err(s2_cmd(soc, "ls BAD*NAME")) == "not found"


def test_cd_and_pwd():
    soc, _ = boot_s2(FILES)
    assert s2_cmd(soc, "pwd") == "/\n"
    assert s2_cmd(soc, "cd VUX9K/APPS") == ""
    assert s2_cmd(soc, "pwd") == "/VUX9K/APPS\n"
    assert s2_cmd(soc, "ls") == ls_line("HELLO.BIN", 40) + "\n"
    assert s2_cmd(soc, "cat ../../README.TXT") == "hi\n"
    assert s2_cmd(soc, "cd ..") == ""
    assert s2_cmd(soc, "pwd") == "/VUX9K\n"
    assert s2_cmd(soc, "cd /DOCS") == ""
    assert s2_cmd(soc, "pwd") == "/DOCS\n"
    assert s2_cmd(soc, "cd ../../..") == ""
    assert s2_cmd(soc, "pwd") == "/\n"
    assert err(s2_cmd(soc, "cd README.TXT")) == "not a directory"
    assert err(s2_cmd(soc, "cd NOPE")) == "not found"
    assert s2_cmd(soc, "pwd") == "/\n"


def test_cd_past_the_longest_path():
    deep = "/".join(["ABCDEFGH"] * 11)  # 99 characters with the leading /
    soc, _ = boot_s2({f"{deep}/X.TXT": b"x\n"})
    assert err(s2_cmd(soc, f"cd {deep}")) == "path too long"
    assert s2_cmd(soc, "pwd") == "/\n"
    assert s2_cmd(soc, "cd " + "/".join(["ABCDEFGH"] * 10)) == ""
    assert s2_cmd(soc, "pwd") == "/" + "/".join(["ABCDEFGH"] * 10) + "\n"
    assert s2_cmd(soc, "cat ABCDEFGH/X.TXT") == "x\n"


def test_cat_and_hexdump():
    soc, _ = boot_s2(FILES)
    assert s2_cmd(soc, "cat README.TXT") == "hi\n"
    assert s2_cmd(soc, "cat NOEOL.TXT") == "no line end\n"
    assert s2_cmd(soc, "cat EMPTY.DAT") == ""
    assert s2_cmd(soc, "hexdump VUX9K/APPS/HELLO.BIN") == hexdump(bytes(range(40)))
    assert s2_cmd(soc, "hexdump EMPTY.DAT") == ""
    assert err(s2_cmd(soc, "cat DOCS")) == "is a directory"
    assert err(s2_cmd(soc, "hexdump NOPE")) == "not found"


def test_hexdump_across_sectors():
    data = bytes((i * 7) & 0xFF for i in range(1300))
    soc, _ = boot_s2({"BIG.BIN": data})
    assert s2_cmd(soc, "hexdump BIG.BIN") == hexdump(data)


def test_mkdir_rm_rmdir_mv():
    soc, _ = boot_s2(FILES)
    assert s2_cmd(soc, "mkdir TEST") == ""
    assert err(s2_cmd(soc, "mkdir test")) == "exists"
    assert err(s2_cmd(soc, "mkdir README.TXT")) == "exists"
    assert err(s2_cmd(soc, "mkdir NOPE/X")) == "not found"
    assert err(s2_cmd(soc, "mkdir TOOLONGNAME")) == "bad name"
    assert err(s2_cmd(soc, "mkdir README.TXT/X")) == "not a directory"
    assert s2_cmd(soc, "mkdir TEST/SUB/") == ""
    assert s2_cmd(soc, "ls TEST") == ls_line("SUB", None) + "\n"
    # mv: into a directory (same name), to a new name there, a rename in place
    assert s2_cmd(soc, "mv README.TXT TEST") == ""
    assert s2_cmd(soc, "mv TEST/README.TXT TEST/SUB/R.TXT") == ""
    assert s2_cmd(soc, "mv TEST/SUB/R.TXT TEST/SUB/R2.TXT") == ""
    assert s2_cmd(soc, "cat test/sub/r2.txt") == "hi\n"
    assert err(s2_cmd(soc, "cat README.TXT")) == "not found"
    assert err(s2_cmd(soc, "mv TEST/SUB/R2.TXT NOEOL.TXT")) == "exists"
    assert err(s2_cmd(soc, "mv DOCS TEST")) == "is a directory"
    assert err(s2_cmd(soc, "mv NOPE TEST")) == "not found"
    assert err(s2_cmd(soc, "mv NOEOL.TXT NOPE/X.TXT")) == "not found"
    assert err(s2_cmd(soc, "mv NOEOL.TXT TOOLONGNAME.TXT")) == "bad name"
    assert err(s2_cmd(soc, "mv .. X")) == "bad name"
    # rmdir: only empty directories, not files, not `.`, not the working directory
    assert err(s2_cmd(soc, "rmdir TEST")) == "not empty"
    assert err(s2_cmd(soc, "rmdir NOEOL.TXT")) == "not a directory"
    assert err(s2_cmd(soc, "rmdir .")) == "bad name"
    assert err(s2_cmd(soc, "rmdir /")) == "bad name"
    assert err(s2_cmd(soc, "rmdir NOPE")) == "not found"
    assert s2_cmd(soc, "cd TEST/SUB") == ""
    assert s2_cmd(soc, "rm R2.TXT") == ""
    assert err(s2_cmd(soc, "rmdir ../SUB")) == "is the current directory"
    assert s2_cmd(soc, "cd /") == ""
    assert s2_cmd(soc, "rmdir TEST/SUB") == ""
    assert s2_cmd(soc, "rmdir TEST") == ""
    # rm: files only
    assert err(s2_cmd(soc, "rm DOCS")) == "is a directory"
    assert err(s2_cmd(soc, "rm NOPE")) == "not found"
    assert err(s2_cmd(soc, "rm DOCS/..")) == "bad name"
    assert s2_cmd(soc, "rm DOCS/A.TXT") == ""
    assert s2_cmd(soc, "ls DOCS") == ""
    assert sorted(s2_cmd(soc, "ls").splitlines()) == [
        ls_line("DOCS", None),
        ls_line("EMPTY.DAT", 0),
        ls_line("NOEOL.TXT", 11),
        ls_line("VUX9K", None),
    ]


def lfn_card() -> bytes:
    """mkfs.fat/mtools' card with long names (firmware/fw_common/tests/fat_images/), no
    \\VUX9K\\ on it: the tests RAM-boot the second stage (fixture `tool`, ram_boot=True)."""
    return fixture("mkfs_lfn")


def test_long_names_are_listed_and_found(tool):
    soc, ser = tool(lfn_card(), ram_boot=True)
    out = vux_tool.s2_cmd(ser, "ls")
    assert ls_line("ABCDEF~1.KL", 9, "ABCDEFGHIJ.KL") in out, out
    assert "  Mixed Case.Txt" in out and "  Projects Folder" in out
    # A UTF-16 character outside ASCII reads as ?
    assert "  ?n?c?d? file.txt" in out, out
    # Long names are found (case ignored), in paths too
    assert vux_tool.s2_cmd(ser, "cat abcdefghij.kl") == "thirteen\n"
    folder = next(ln for ln in out.splitlines() if ln.endswith("  Projects Folder")).split()[0]
    sub = vux_tool.s2_cmd(ser, f"ls {folder}")
    assert "  A Very Long File Name That Needs Several Entries.txt" in sub, sub
    assert vux_tool.s2_cmd(ser, f"cd {folder}/sub") == ""
    assert vux_tool.s2_cmd(ser, "pwd") == f"/{folder}/sub\n"
    assert vux_tool.s2_cmd(ser, "cat x.txt") != ""
    # A long name is dropped when the file moves
    long = next(ln for ln in sub.splitlines() if "Very Long" in ln).split()[0]
    vux_tool.s2_cmd(ser, f"mv ../{long} /MOVED.TXT")
    assert ls_line("MOVED.TXT", 700) in vux_tool.s2_cmd(ser, "ls /")


class OneReadPort:
    """A port that answers a command line with its echo, the reply and the prompt in a
    single read, as the second stage from the PSRAM's caches can."""

    def __init__(self, reply: str):
        self.reply = reply
        self.out = b""

    def write(self, data: bytes):
        line = data.decode().rstrip("\r")
        self.out += f"{line}\n{self.reply}{S2_PROMPT.decode()}".encode()

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data, self.out = self.out[:n], self.out[n:]
        return data


def test_s2_cmd_takes_a_reply_that_came_with_the_echo():
    """Found by the board suite on the emulator once the second stage ran from the caches:
    s2_cmd waited for a second prompt after the read that held the echo and the prompt."""
    assert vux_tool.s2_cmd(OneReadPort("[ERR] not found\n"), "ls X", timeout=1.0, check=False) == ("[ERR] not found\n")
    assert vux_tool.s2_cmd(OneReadPort(""), "mkdir A", timeout=1.0) == ""


def test_put_and_cat_round_trip(tool):
    soc, ser = tool(card({S2: stage2(), "README.TXT": b"old\n"}))
    vux_tool.s2_cmd(ser, "mkdir TEST")
    text = bytes(range(32, 127)) * 30 + b"\n"
    assert vux_tool.put(ser, "TEST/DATA.TXT", text) == f"[PUT] OK {len(text)} bytes"
    assert vux_tool.s2_cmd(ser, "cat TEST/DATA.TXT").encode() == text
    # Binary, byte for byte (the reply read raw)
    blob = bytes((i * 151 + 7) & 0xFF for i in range(1500))
    vux_tool.put(ser, "test/blob.bin", blob)
    mark = len(soc.uart_received())
    ser.write(b"cat TEST/BLOB.BIN\r")
    vux_tool.read_until(ser, (S2_PROMPT.decode(),), 5.0)
    raw = soc.uart_received()[mark:]
    assert raw == b"cat TEST/BLOB.BIN\n" + blob + (b"" if blob.endswith(b"\n") else b"\n") + S2_PROMPT
    # An existing file is replaced
    assert vux_tool.put(ser, "README.TXT", b"new\n") == "[PUT] OK 4 bytes"
    assert vux_tool.s2_cmd(ser, "cat README.TXT") == "new\n"
    assert vux_tool.s2_cmd(ser, "ls").count("README.TXT") == 1
    # A ~PUT.TMP a cut put left behind goes; ~PUT.TMP itself can be put
    assert vux_tool.put(ser, "~PUT.TMP", b"stale\n") == "[PUT] OK 6 bytes"
    assert vux_tool.put(ser, "~PUT.TMP", b"again\n") == "[PUT] OK 6 bytes"
    assert vux_tool.s2_cmd(ser, "cat ~PUT.TMP") == "again\n"
    vux_tool.put(ser, "README.TXT", b"newer\n")
    listing = vux_tool.s2_cmd(ser, "ls")
    assert "~PUT.TMP" not in listing and listing.count("README.TXT") == 1
    assert vux_tool.s2_cmd(ser, "cat README.TXT") == "newer\n"
    # Refused before the transfer: a directory, a bad name, a missing directory
    for path, why in [("TEST", "is a directory"), ("TOOLONGNAME.TXT", "bad name"), ("NOPE/X.TXT", "not found")]:
        with pytest.raises(vux_tool.UploadError, match=f"\\[ERR\\] {why}"):
            vux_tool.put(ser, path, b"x")
    # A broken transfer: upload_v2's own report, and the prompt again
    with pytest.raises(vux_tool.UploadError, match="CRC mismatch"):
        ser.write(b"put CRC.TXT\r")
        vux_tool.send_upload_v2(ser, b"data", crc_override=1)
    vux_tool.read_until(ser, (S2_PROMPT.decode(),), 5.0)
    assert "CRC.TXT" not in vux_tool.s2_cmd(ser, "ls")


def full_card(spare_clusters: int) -> bytes:
    """A card with the second stage and a filler file leaving `spare_clusters` free."""
    base = {S2: stage2()}
    free = struct.unpack_from("<I", card(base), (FAT_LBA + 1) * 512 + 488)[0]
    return card(base | {"FILLER.BIN": bytes((free - spare_clusters) * 512)})


def test_disk_full(tool):
    soc, ser = tool(full_card(2))
    assert "[FAT] FAT32 at LBA 2048, 512 B clusters, 2 free (1 KB)" in vux_tool.s2_cmd(ser, "info")
    with pytest.raises(vux_tool.S2Error, match="card full"):
        vux_tool.put(ser, "BIG.BIN", bytes(3 * 512))
    assert "BIG.BIN" not in vux_tool.s2_cmd(ser, "ls")
    assert vux_tool.put(ser, "SMALL.BIN", bytes(2 * 512)) == "[PUT] OK 1024 bytes"
    # Replacing it needs room for both: the old one stays
    with pytest.raises(vux_tool.S2Error, match="card full"):
        vux_tool.put(ser, "SMALL.BIN", bytes([1]) * 512)
    assert vux_tool.s2_cmd(ser, "cat SMALL.BIN") == "\0" * 1024 + "\n"  # cat ends the line
    with pytest.raises(vux_tool.S2Error, match="card full"):
        vux_tool.s2_cmd(ser, "mkdir NEWDIR")
    vux_tool.s2_cmd(ser, "rm SMALL.BIN")
    assert "2 free (1 KB)" in vux_tool.s2_cmd(ser, "info")


def test_info():
    files = {"README.TXT": b"hi\n"}
    for sectors in (1, 8):
        img = card({S2: stage2()} | files, cluster_sectors=sectors)
        free = struct.unpack_from("<I", img, (FAT_LBA + 1) * 512 + 488)[0]
        soc, _ = boot_s2(image=img)
        out = s2_cmd(soc, "info")
        kb = free * sectors // 2
        assert out == (
            "[SD] SDHC card\n"
            f"[FAT] FAT32 at LBA {FAT_LBA}, {512 * sectors} B clusters, {free} free ({kb} KB)\n"
            f"[S1] v{STAGE1_VERSION} [S2] v{STAGE2_VERSION}\n"
        ), out
    soc, _ = boot_s2(sd_card={"sdhc": False, "strict": True})
    assert s2_cmd(soc, "info").startswith("[SD] SDSC card\n")


def test_card_errors():
    img = card({S2: stage2(), "README.TXT": b"hi\n"})
    # The root directory's sector fails after the first stage's read of it (a failed read
    # waits for the data token through all of SdCard::read_block's 50000 tries: ~30M
    # cycles from the PSRAM)
    root = FAT_LBA + 32 + 2 * struct.unpack_from("<I", img, FAT_LBA * 512 + 36)[0]
    soc, _ = boot_s2(image=img, sd_card={"fail_after": [(root, 1)]})
    assert err(s2_cmd(soc, "ls", timeout_cycles=4 * BOOT_CYCLES)) == f"card error at LBA {root}"
    # Writes refused (the first stage writes nothing)
    soc, _ = boot_s2(image=img, sd_card={"write_reject": True})
    assert err(s2_cmd(soc, "mkdir X")).startswith("card error at LBA ")
    assert err(s2_cmd(soc, "rm README.TXT")).startswith("card error at LBA ")
    assert "X" not in s2_cmd(soc, "ls").split()


def test_no_card(tool):
    """RAM-booted without a card: the second stage tries to initialize one and says
    there is none."""
    soc, ser = tool(None, ram_boot=True, card_present=False)
    assert err(vux_tool.s2_cmd(ser, "ls", timeout=60.0, check=False)) == "no SD card"
    with pytest.raises(vux_tool.UploadError, match=r"\[INSTALL\] card error at LBA 0"):
        vux_tool.install_stage2(ser, stage2(), timeout=60.0)


# ----- install ------------------------------------------------------------------------


def test_install_on_a_card_without_the_directory(tool):
    soc, ser = tool(lfn_card(), ram_boot=True)
    assert "VUX9K" not in vux_tool.s2_cmd(ser, "ls")
    assert vux_tool.install_stage2(ser, stage2(version=7)) == "[INSTALL] OK"
    assert ls_line("STAGE2.BIN", len(stage2())) in vux_tool.s2_cmd(ser, "ls VUX9K")
    # Unchecked (tests): a broken one, the first stage then boots STAGE2.OLD (v7 again)
    assert vux_tool.install_stage2(ser, stage2(version=7)) == "[INSTALL] OK"
    assert vux_tool.install_stage2(ser, stage2(crc_override=0), unchecked=True) == "[UNCHECKED] OK"
    ser.write(b"reset\r")
    got, out = vux_tool.read_until(ser, (S2_PROMPT.decode(),), 10.0)
    assert got and "[BOOT] STAGE2.OLD (STAGE2.BIN missing or broken) v7 " in out, out
    with pytest.raises(vux_tool.UploadError, match=r"\[INSTALL\] loads outside 80700000-807FFFFF"):
        vux_tool.install_stage2(ser, stage2(load_addr=0x8000_0000))


def test_unchecked_install_needs_the_directory(tool):
    soc, ser = tool(lfn_card(), ram_boot=True)
    with pytest.raises(vux_tool.UploadError, match=r"\[UNCHECKED\] not found"):
        vux_tool.install_stage2(ser, stage2(), unchecked=True)


# ----- run ----------------------------------------------------------------------------

PSRAM_APP = 0x8000_0000


def test_run_from_the_psram():
    soc, _ = boot_s2({"VUX9K/APPS/HELLO.BIN": v4(app(PSRAM_APP + 0x100, "Hello\n"), load_addr=PSRAM_APP + 0x100,
                                                   name="hello", version=3)})  # fmt: skip
    soc.load_dram_words([0xDEADBEEF] * 2048)
    out = s2_cmd(soc, "run VUX9K/APPS/HELLO.BIN", until=b"Hello\n", timeout_cycles=RUN_CYCLES)
    size = len(app(PSRAM_APP + 0x100, "Hello\n"))
    assert out.startswith(f'[RUN] v3 "hello" {size} B, PSRAM, starting at 0x{PSRAM_APP + 0x100:08X}\n\n'), out
    soc.run(ms(1))
    assert PSRAM_APP + 0x100 <= soc.pc < PSRAM_APP + 0x100 + size
    # a0-a2 as the first stage hands them to the second stage; D-RAM zeroed
    assert soc.regs[10:13] == [1, FAT_LBA, STAGE1_VERSION]
    assert all(soc.dram_word(i) == 0 for i in range(2048))
    assert s1_intact(soc)


def test_run_refuses_rv32_in_i_ram():
    """RV32 runs from the PSRAM only (second stage v3): a file linked at 0, as the Zephyr
    board's I-RAM build made them, is refused after it loaded, the first stage stays in
    I-RAM and the prompt comes back."""
    soc, _ = boot_s2({"APP.BIN": v4(app(0, "Iram\n"), load_addr=0, name="iram")})
    out = s2_cmd(soc, "run APP.BIN", timeout_cycles=RUN_CYCLES)
    assert out == "[ERR] loads outside 80000000-806FFFFF (RV32), 0-3FFF (Hack, at a word)\n", out
    assert "Iram" not in out and "[RUN]" not in out
    assert s1_intact(soc)
    assert "VUX9K" in s2_cmd(soc, "ls")


def test_run_hack_from_i_ram():
    assert os.path.exists(HACK_BIN), f"{HACK_BIN} missing; run `make build-hack` first"
    with open(HACK_BIN, "rb") as f:
        hack = v4(f.read(), mode="hack", name="Hack demo")
    soc, _ = boot_s2({"VUX9K/APPS/HACK.BIN": hack})
    out = s2_cmd(soc, "run VUX9K/APPS/HACK.BIN", until=HACK_DONE, timeout_cycles=RUN_CYCLES + 20_000_000)
    assert f'[RUN] v1 "Hack demo" {len(hack) - 64} B, I-RAM (Hack), starting at 0x00000000\n\n' in out, out
    assert "[FAIL]" not in out
    assert not soc.riscv_mode


def bad_mode(file: bytes) -> bytes:
    b = bytearray(file)
    b[8:12] = (2).to_bytes(4, "little")
    return bytes(b)


PS_APP = app(PSRAM_APP, "x")
REFUSED = {
    "MAGIC.BIN": (v4(PS_APP, load_addr=PSRAM_APP, magic_override=0x12345678), "not a VUX9 v4 file"),
    "CRC.BIN": (v4(PS_APP, load_addr=PSRAM_APP, crc_override=0), "CRC mismatch"),
    "HACKCRC.BIN": (v4(b"\x12\x34" * 8, mode="hack", crc_override=1), "CRC mismatch"),
    "SIZE.BIN": (v4(PS_APP, load_addr=PSRAM_APP)[:-4], "file size is not the header's"),
    "DRAM.BIN": (v4(PS_APP, load_addr=0x2000_0000), "loads outside"),
    "S2WIN.BIN": (v4(PS_APP, load_addr=0x8070_0000), "loads outside"),
    # RV32 in I-RAM (second stage v3: the PSRAM only), and Hack past I-RAM's end
    "RVIRAM.BIN": (v4(app(0, "x"), load_addr=0), "loads outside"),
    "BIGHACK.BIN": (v4(bytes(0x4002), mode="hack"), "loads outside"),
    "HACKODD.BIN": (v4(b"\x12\x34" * 8, mode="hack", load_addr=2), "loads outside"),
    "MODE.BIN": (bad_mode(v4(PS_APP, load_addr=PSRAM_APP)), "not RV32 or Hack, mode 2"),
    "ENTRY.BIN": (v4(PS_APP, load_addr=PSRAM_APP, entry_point=PSRAM_APP + 2), "bad entry point 80000002"),
    # Word-aligned, but past the payload
    "ENTRYOUT.BIN": (
        v4(PS_APP, load_addr=PSRAM_APP, entry_point=PSRAM_APP + len(PS_APP)),
        f"bad entry point {PSRAM_APP + len(PS_APP):08X}",
    ),
    "EMPTY.BIN": (v4(b"", load_addr=PSRAM_APP), "empty"),
    "SHORT.BIN": (b"VUX9", "not a VUX9 v4 file"),
}


def test_run_refusals_leave_the_first_stage():
    soc, _ = boot_s2(dict(REFUSED_FILES := {f"BAD/{k}": v[0] for k, v in REFUSED.items()}))
    assert REFUSED_FILES
    for name, (_, why) in REFUSED.items():
        got = err(s2_cmd(soc, f"run BAD/{name}"))
        assert got.startswith(why), (name, got)
        assert s1_intact(soc), name
    assert err(s2_cmd(soc, "run BAD")) == "is a directory"
    assert err(s2_cmd(soc, "run NOPE.BIN")) == "not found"
    assert err(s2_cmd(soc, "run BAD/DRAM.BIN")) == "loads outside 80000000-806FFFFF (RV32), 0-3FFF (Hack, at a word)"
    # Still at the prompt, and `reset` finds the first stage as it was
    out = s2_cmd(soc, "reset", until=S2_PROMPT)
    assert out.startswith("[RESET] Soft reset\n") and BANNER in out, out


def test_reset_back_to_the_first_stage():
    soc, _ = boot_s2()
    soc.run(50 * UART_BIT)
    mark = len(soc.uart_received())
    soc.uart_send(b"reset\r\n")  # the line feed is not a key for the first stage
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES, start=mark)
    assert "[RESET] Soft reset" in out and BANNER in out and "[SAFE MODE]" not in out, out
    # And to its prompt with vux_tool
    soc, _ = boot_s2()
    out = s2_cmd(soc, "reset", until=BANNER.encode())
    soc.uart_send(b"\r")
    assert "[SAFE MODE] Key received" in wait_for(soc, S1_PROMPT, BOOT_CYCLES, start=len(soc.uart_received()) - 1)


def test_trap_in_an_application_is_reported():
    """A PSRAM application that sets no trap vector of its own: the second stage's
    reports its exception."""
    code = struct.pack("<I", 0)  # an all-zero word: illegal instruction
    soc, _ = boot_s2({"TRAP.BIN": v4(code, load_addr=PSRAM_APP)})
    out = s2_cmd(soc, "run TRAP.BIN", until=b"Reset the board.\n", timeout_cycles=RUN_CYCLES)
    assert f"[TRAP] mcause=0x00000002 mepc=0x{PSRAM_APP:08X} mtval=0x00000000" in out, out


def test_run_app_through_vux_tool(tool):
    hello = v4(app(PSRAM_APP, "Hi from the PSRAM\n"), load_addr=PSRAM_APP, name="hello")
    soc, ser = tool(card({S2: stage2()}))
    vux_tool.s2_cmd(ser, "mkdir VUX9K/APPS")
    vux_tool.put(ser, "VUX9K/APPS/HELLO.BIN", hello)
    # Refused: the [ERR] line, and the prompt is back
    with pytest.raises(vux_tool.S2Error, match="not found"):
        vux_tool.run_app(ser, "NOPE.BIN")
    out = vux_tool.run_app(ser, "VUX9K/APPS/HELLO.BIN")
    run_line = f'[RUN] v1 "hello" {len(hello) - 64} B, PSRAM, starting at 0x80000000\n'
    assert run_line in out, out
    # The application's output that arrived with the [RUN] line is kept, not dropped
    assert vux_tool.after_run(out) == out[out.index(run_line) + len(run_line) :]
    got, rest = vux_tool.read_until(ser, ("Hi from the PSRAM\n",), 5.0, vux_tool.after_run(out))
    assert got, rest


def test_run_app_keeps_output_read_with_the_run_line():
    """An application in the PSRAM prints at once, so its first bytes can come in the same
    read as the [RUN] line; run_app once cut them off (the board lost the Zephyr demo's
    banner that way)."""
    run_line = '[RUN] v1 "zephyr" 13466 B, PSRAM, starting at 0x80000000\n'

    class OneRead:
        def __init__(self):
            self.chunks = [b"run X\r\n" + run_line.encode() + b"*** Booting Zephyr\r\n", b"[late]\n"]

        def read(self, n):
            return self.chunks.pop(0) if self.chunks else b""

        def reset_input_buffer(self):
            pass

        def write(self, data):
            pass

        def flush(self):
            pass

        @property
        def in_waiting(self):
            return 0

    ser = OneRead()
    out = vux_tool.run_app(ser, "X", timeout=1.0)
    assert vux_tool.after_run(out) == "*** Booting Zephyr\r\n"
    got, rest = vux_tool.read_until(ser, ("[late]\n",), 1.0, vux_tool.after_run(out))
    assert got and rest == "*** Booting Zephyr\r\n[late]\n"
    # read_until finds a token already in the text it is given without reading
    assert vux_tool.read_until(ser, ("Zephyr",), 0.0, vux_tool.after_run(out))[0] == "Zephyr"


def test_enter_s2_from_the_first_stage(tool):
    soc, ser = tool(card({S2: stage2()}))
    vux_tool.enter_s1(ser)
    assert S2_BANNER in vux_tool.enter_s2(ser)
    assert "STAGE2.BIN" in vux_tool.s2_cmd(ser, "ls VUX9K")
    # At the second stage's prompt already
    assert vux_tool.enter_s2(ser).endswith(S2_PROMPT.decode())


def test_crc_of_a_put_file_matches(tool):
    """put then the card read back by the first stage's view of it: the CRC of the
    written file is the sender's (the second stage wrote what arrived)"""
    soc, ser = tool(card({S2: stage2()}))
    data = bytes((i * 31) & 0xFF for i in range(2600))
    vux_tool.put(ser, "VUX9K/STAGE2.OLD", v4(data, load_addr=0x8070_0000, name="old"))
    vux_tool.enter_s1(ser)
    out = vux_tool.send_cmd_and_wait(ser, "i", prompt=S1_PROMPT.decode())
    assert f'STAGE2.OLD: v1 "old" {len(data)} B [OK]' in out, out


# ----- what only the second stage's own runs reach -------------------------------------


@pytest.mark.parametrize(
    "kind",
    [{}, {"sdhc": False, "strict": True}, {"mute_cmds": [8], "sdhc": False, "strict": True}, {"mute_cmds": [55]}],
    ids=["sdhc", "sdsc", "sd_v1", "no_cmd55"],
)
def test_card_inserted_after_a_ram_boot(tool, kind):
    """RAM-booted without a card (a1 = 0), the second stage initializes the card itself
    once one is there."""
    soc, ser = tool(None, ram_boot=True, card_present=False)
    assert err(vux_tool.s2_cmd(ser, "ls", timeout=60.0, check=False)) == "no SD card"
    soc.sd_insert(card({"README.TXT": b"hi\n"}), **kind)
    assert vux_tool.s2_cmd(ser, "cat README.TXT", timeout=60.0) == "hi\n"
    sd = "SDHC" if kind.get("sdhc", True) else "SDSC"
    assert vux_tool.s2_cmd(ser, "info").startswith(f"[SD] {sd} card\n")
    assert not [v for v in soc.sd_violations if not v.startswith("power-up")], soc.sd_violations


def test_cards_without_a_fat32_volume(tool):
    img = bytearray(card({"README.TXT": b"hi\n"}))
    cases = [
        (bytes(len(img)), "no 55AA signature (MBR or boot sector)"),
        (bytes(img[:450]) + b"\x06" + bytes(img[451:]), "not FAT32 (reformat the card as FAT32)"),
        (bytes(img[: FAT_LBA * 512 + 44]) + bytes(4) + bytes(img[FAT_LBA * 512 + 48 :]), "bad FAT32 boot sector"),
    ]
    for image, why in cases:
        soc, ser = tool(image, ram_boot=True)
        assert err(vux_tool.s2_cmd(ser, "ls", check=False)) == why


def test_broken_chain_is_reported():
    img = bytearray(card({S2: stage2(), "DATA.BIN": bytes(2000)}))
    at = FAT_LBA * 512
    e = img.find(b"DATA    BIN", at)
    first = int.from_bytes(img[e + 26 : e + 28], "little")
    fat = at + int.from_bytes(img[at + 14 : at + 16], "little") * 512
    img[fat + 4 * first : fat + 4 * first + 4] = (1).to_bytes(4, "little")
    soc, _ = boot_s2(image=bytes(img))
    assert err(s2_cmd(soc, "cat DATA.BIN")) == f"bad cluster chain at cluster {first}"


def test_directories_grow_and_long_name_runs_are_removed(tool):
    """mkfs.fat/mtools' card: a long name whose entries cross a sector, removed with
    them; a directory that grows by a cluster for one more entry."""
    soc, ser = tool(fixture("mkfs_s1"), ram_boot=True)
    listing = vux_tool.s2_cmd(ser, "ls VUX9K")
    longs = [ln.split()[0] for ln in listing.splitlines() if "~" in ln.split()[0]]
    assert longs, listing
    for name in longs:
        vux_tool.s2_cmd(ser, f"rm VUX9K/{name}")
    assert "~" not in vux_tool.s2_cmd(ser, "ls VUX9K")
    vux_tool.s2_cmd(ser, "mkdir MANY")
    for i in range(16):  # `.`, `..` and 14 fill the first cluster's sector
        vux_tool.put(ser, f"MANY/F{i:02}.TXT", b"%d\n" % i)
    assert len(vux_tool.s2_cmd(ser, "ls MANY").splitlines()) == 16
    assert vux_tool.s2_cmd(ser, "cat MANY/F15.TXT") == "15\n"


def test_big_clusters():
    img = card({S2: stage2()}, cluster_sectors=8)
    soc, _ = boot_s2(image=img)
    assert s2_cmd(soc, "mkdir D") == ""
    assert s2_cmd(soc, "ls D") == ""
    assert "4096 B clusters" in s2_cmd(soc, "info")


def test_install_over_leftovers(tool):
    """An install cut before it renamed STAGE2.NEW, and one over a broken STAGE2.BIN."""
    soc, ser = tool(card({S2: stage2(version=2)}))
    vux_tool.put(ser, "VUX9K/STAGE2.NEW", b"stale")
    assert vux_tool.install_stage2(ser, stage2(version=3)) == "[INSTALL] OK"
    assert "STAGE2.NEW" not in vux_tool.s2_cmd(ser, "ls VUX9K")
    vux_tool.install_stage2(ser, stage2(crc_override=0), unchecked=True)
    assert vux_tool.install_stage2(ser, stage2(version=4)) == "[INSTALL] OK"
    vux_tool.enter_s1(ser)
    out = vux_tool.send_cmd_and_wait(ser, "i", prompt=S1_PROMPT.decode())
    # The broken .BIN was replaced without touching .OLD (v2)
    assert "STAGE2.BIN: v4 " in out and "STAGE2.OLD: v2 " in out, out


def test_put_transfer_errors(tool):
    """What `put` says when the host breaks transfer v2 (upload_v2 in the second
    stage), and that bytes before the transfer are dropped."""
    soc, ser = tool(card({S2: stage2()}))

    def attempt(length=None, data=b"") -> str:
        ser.write(b"put X.TXT\r")
        vux_tool.read_until(ser, ("[READY-V2]",), 5.0)
        if length is not None:
            ser.write(length.to_bytes(4, "little"))
        ser.write(data)
        got, out = vux_tool.read_until(ser, (S2_PROMPT.decode(),), 12.0)  # past the 5 s byte timeout
        assert got, out
        return out

    assert "[ERR] Length timeout" in attempt()
    assert "[ERR] Bad length: 7340033" in attempt(0x70_0001)
    assert "[ERR] Timeout at block 0, byte 10" in attempt(100, bytes(10))
    assert "[ERR] CRC timeout" in attempt(10, bytes(10))
    assert "X.TXT" not in vux_tool.s2_cmd(ser, "ls")
    # Bytes after the line (before [READY-V2]) are not the length
    ser.write(b"put X.TXT\rjunk")
    assert vux_tool.send_upload_v2(ser, b"data").startswith("[OK] 4 bytes")
    vux_tool.read_until(ser, (S2_PROMPT.decode(),), 5.0)
    assert vux_tool.s2_cmd(ser, "cat X.TXT") == "data\n"


def test_full_directory_on_a_full_card(tool):
    """The root's one cluster full of entries and one free cluster: a new file's data
    takes it and its entry finds no room, a new directory's cluster too; both give the
    cluster back."""
    base = {S2: stage2()} | {f"F{i:02}.TXT": b"" for i in range(12)}
    free = struct.unpack_from("<I", card(base), (FAT_LBA + 1) * 512 + 488)[0]
    # The label, VUX9K, 12 files, FILLER.BIN: 15 entries of 16; one more file fills it
    soc, ser = tool(card(base | {"FILLER.BIN": bytes((free - 1) * 512), "LAST.TXT": b""}))
    assert "1 free (0 KB)" in vux_tool.s2_cmd(ser, "info")
    # (The search for a second free cluster reads every FAT entry through fat_entry:
    # ~5,000 cycles each from the PSRAM)
    with pytest.raises(vux_tool.S2Error, match="card full"):
        vux_tool.s2_cmd(ser, "mkdir NEWDIR", timeout=20.0)
    assert "1 free (0 KB)" in vux_tool.s2_cmd(ser, "info")
    with pytest.raises(vux_tool.S2Error, match="card full"):
        vux_tool.put(ser, "NEW.TXT", b"x", timeout=20.0)
    assert "1 free (0 KB)" in vux_tool.s2_cmd(ser, "info")


def test_long_names_across_sectors_of_a_cluster(tool):
    """mkfs.fat's card with 8-sector clusters: removing long names whose entries cross a
    sector inside a cluster."""
    soc, ser = tool(fixture("mkfs_s8_1fat"), ram_boot=True)
    listing = vux_tool.s2_cmd(ser, "ls VUX9K")
    longs = [ln.split()[0] for ln in listing.splitlines() if "~" in ln.split()[0]]
    assert longs, listing
    for name in longs:
        vux_tool.s2_cmd(ser, f"rm VUX9K/{name}")
    assert "~" not in vux_tool.s2_cmd(ser, "ls VUX9K")
