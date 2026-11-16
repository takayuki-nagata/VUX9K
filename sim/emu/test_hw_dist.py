# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Host-side checks of the board-test script (scripts/test_hardware.py), no board.

`test_hardware.py --dist DIR` checks the first stage's version against the one DIR ships
(MANIFEST.json), not the working tree's main.rs: a dist of another commit has another
first stage in its bitstream, and installs its own second stage and demos. The demos'
expectations are the emulator tests'. With VUX9K_HW_SUITE_EMU=1 the whole suite also runs
against `vux9k-emu --tcp` (~3 min at the board's speed; the tests that need a bitstream
load are skipped there).
"""

import json
import os
import socket
import subprocess
import sys
import threading

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_hardware  # noqa: E402


def make_dist(tmp_path, s1_version):
    """The files test_hardware needs from a dist, with a MANIFEST naming s1_version."""
    for rel in test_hardware.DIST_FILES.values():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"\0" * 8)
    (tmp_path / test_hardware.DIST_APPS_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"stage1_version": s1_version}))
    return str(tmp_path)


def run_main(monkeypatch, argv):
    """test_hardware.main() with argv, the board replaced; the S1_VERSION and FILES it ran with."""
    seen: dict = {}

    def suite(port="auto", baud=115200, loader=None, reload=True):
        seen.update(s1=test_hardware.S1_VERSION, files=dict(test_hardware.FILES), reload=reload)
        return True

    monkeypatch.setattr(test_hardware, "S1_VERSION", test_hardware.S1_VERSION)
    monkeypatch.setattr(test_hardware, "FILES", dict(test_hardware.FILES))
    monkeypatch.setattr(test_hardware, "run_hardware_test_suite", suite)
    monkeypatch.setattr(sys, "argv", ["test_hardware.py", *argv])
    test_hardware.main()
    return seen


def test_dist_stage1_version_comes_from_the_manifest(tmp_path, monkeypatch):
    own = test_hardware.stage1_version()
    dist = make_dist(tmp_path, own + 7)
    assert test_hardware.dist_stage1_version(dist) == own + 7
    seen = run_main(monkeypatch, ["--dist", dist])
    assert seen["s1"] == own + 7
    assert seen["files"]["stage2"] == os.path.join(dist, "sd", "VUX9K", "STAGE2.BIN")
    assert seen["files"]["apps"] == os.path.join(dist, "sd", "VUX9K", "APPS")
    assert seen["files"]["pack_fs"] == os.path.join(dist, "bitstream", "pack.fs")
    assert seen["reload"]
    assert not run_main(monkeypatch, ["--dist", dist, "--no-reload"])["reload"]


def test_without_dist_the_version_comes_from_main_rs(monkeypatch):
    seen = run_main(monkeypatch, [])
    assert seen["s1"] == test_hardware.stage1_version()


def test_installs_carry_the_test_versions():
    stage2 = test_hardware.vux_tool.build_vux9_v4_image(b"\x13\0\0\0" * 4, name="S2", load_addr=0x80700000)[0]
    data = test_hardware.with_version(stage2, 9001)
    h = test_hardware.vux_tool.ImageHeader.parse(data)
    assert h is not None and (h.version, h.load_addr, h.name.rstrip(b"\0")) == (9001, 0x80700000, b"S2")
    assert data[64:] == stage2[64:]
    out = "[S2] a0=0x00000001 a1=0x00000800 a2=0x00000001\n"
    assert test_hardware.handoff(out) == (1, 2048, 1)
    assert test_hardware.handoff("no handoff") is None


def test_ls_and_hello_parsing():
    out = "SUB          <DIR>\nDATA.TXT          792\nLONGNA~1.TXT        3  Long name.txt\n"
    assert test_hardware.ls_entries(out) == {"SUB": None, "DATA.TXT": 792, "LONGNA~1.TXT": 3}
    assert test_hardware.ls_entries("") == {}
    assert test_hardware.hello_regs("[HELLO] a0=0x00000000 a1=0x00002000 a2=0x00000002\n") == (0, 0x2000, 2)
    assert test_hardware.hello_regs("[S2] a0=0x00000000 a1=0x00002000 a2=0x00000002") is None


def test_demo_expectations_are_the_emulator_tests():
    import test_hack_demo
    import test_hello_demo

    assert test_hardware.HELLO_LAST.encode() == test_hello_demo.LAST_LINE
    assert test_hardware.HACK_DONE.encode() == test_hack_demo.DONE
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_hack_demo.py")).read()
    assert all(f'"{ln}"' in src for ln in test_hardware.HACK_LINES)
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_zephyr_demo.py")).read()
    assert all(ln in src for ln in (*test_hardware.ZEPHYR_LINES, test_hardware.ZEPHYR_END))
    import test_zephyr_bc

    assert test_hardware.BC_LINES == tuple(test_zephyr_bc.SELF_TESTS)
    src = open(os.path.join(REPO_ROOT, "sim", "emu", "test_zephyr_bc.py")).read()
    assert all(f'"{line}"' in src and (not want or f'"{want}"' in src) for line, want in test_hardware.BC_REPL)


def test_irq_echo_files_come_from_the_dist(tmp_path):
    import hw_irq_echo

    dist = make_dist(tmp_path, 1)
    app, pack_fs = hw_irq_echo.resolve_files(hw_irq_echo.parse_args(["--dist", dist]))
    assert app == os.path.join(dist, "sd", "VUX9K", "APPS", "IRQECHO.BIN")
    assert pack_fs == os.path.join(dist, "bitstream", "pack.fs")  # not the working tree's
    app, pack_fs = hw_irq_echo.resolve_files(hw_irq_echo.parse_args(["--bin", "X.BIN"]))
    assert (app, pack_fs) == ("X.BIN", os.path.join(REPO_ROOT, "build", "synth", "pack.fs"))
    with pytest.raises(SystemExit):
        hw_irq_echo.resolve_files(hw_irq_echo.parse_args([]))
    # A raw zephyr.bin is wrapped to load where the board vux9k links it, the PSRAM's start
    h = test_hardware.vux_tool.ImageHeader.parse(hw_irq_echo.v4_file(b"\x13\0\0\0"))
    assert h is not None and (h.mode, h.load_addr) == (test_hardware.vux_tool.MODE_RISCV, 0x8000_0000)


def linux_tree(tmp_path, image_size: int):
    """A make linux-dist tree whose MANIFEST.json gives the Image image_size bytes."""
    tree = tmp_path / "linux-dist"
    (tree / "VUX9K" / "LINUX").mkdir(parents=True)
    (tree / "VUX9K" / "LINUX" / "Image").write_bytes(b"I" * 64)
    (tree / "VUX9K" / "LINUX" / "vux9k.dtb").write_bytes(b"D" * 8)
    (tree / "VUX9K" / "LINUX" / "BIN").mkdir()
    (tree / "VUX9K" / "LINUX" / "BIN" / "prog").write_bytes(b"P" * 4)
    files = {
        "VUX9K/LINUX/BIN/prog": {"size": 4},
        "VUX9K/LINUX/Image": {"size": image_size},
        "VUX9K/LINUX/vux9k.dtb": {"size": 8},
    }
    manifest = {"git_describe": "test", "linux_version": "7.3", "card_files": files, "sizes": {"image": 64}}
    (tree / "MANIFEST.json").write_text(json.dumps(manifest))
    return str(tree)


def test_linux_script_files(tmp_path):
    """hw_linux.py: the bitstream from --pack-fs, else the --dist tree's, else the working
    tree's; the card's files from a make linux-dist tree, refused when they are not the
    sizes its MANIFEST.json gives."""
    import hw_linux

    def pack_fs(*argv):
        return hw_linux.pack_fs_of(hw_linux.parse_args(list(argv)))

    assert pack_fs("--dist", "D", "--pack-fs", "P") == "P"
    assert pack_fs("--dist", "D") == os.path.join("D", "bitstream", "pack.fs")
    assert pack_fs() == os.path.join(REPO_ROOT, "build", "synth", "pack.fs")
    files, sizes = hw_linux.card_files(linux_tree(tmp_path, 64))
    # The kernel's files first (put before the programs), then the card's programs
    assert list(files.items()) == [
        ("VUX9K/LINUX/Image", b"I" * 64),
        ("VUX9K/LINUX/vux9k.dtb", b"D" * 8),
        ("VUX9K/LINUX/BIN/prog", b"P" * 4),
    ]
    assert sizes == {"image": 64}
    with pytest.raises(SystemExit):
        hw_linux.card_files(linux_tree(tmp_path / "bad", 65))


class ShellPort:
    """A port that answers hw_linux.boot as the second stage and the kernel would: the second
    stage's prompt, `linux` with linux_line and the console up to the shell's prompt, and
    each command line with its echo (each write's as it comes, as busybox's line editing
    echoes keys), its reply from replies and the prompt; MicroPython's REPL (repl, the line
    and its value, Ctrl-D back to the shell)."""

    def __init__(self, linux_line: str, console: str, replies: dict[str, str], repl: str = "MicroPython v1.29.0"):
        import linux_checks

        self.linux_line, self.console, self.replies, self.repl_banner = linux_line, console, replies, repl
        self.repl_cmd = linux_checks.MPY_REPL
        self.out = b"s2> "
        self.typed = b""
        self.shell = False
        self.repl = False

    def write(self, data: bytes):
        self.typed += data
        if self.shell:
            self.out += data.rstrip(b"\r\n")
        if self.repl and b"\x04" in self.typed:
            self.typed, self.repl = b"", False
            self.out += b"\r\n/ # "
        elif self.typed.endswith((b"\r", b"\n")):
            line, self.typed = self.typed.strip().decode(), b""
            if line == "linux" and not self.shell:
                self.out += f"linux\n{self.linux_line}\n\n{self.console}/ # ".encode()
                self.shell = True
            elif self.repl:
                self.out += f"\r\n{self.replies.get('>>> ' + line, '')}>>> ".encode()
            elif line == self.repl_cmd:
                self.out += f"\r\n{self.repl_banner} on 2026-11-16; linux\r\n>>> ".encode()
                self.repl = True
            else:
                self.out += f"\r\n{self.replies.get(line, '')}/ # ".encode()

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data, self.out = self.out[:n], self.out[n:]
        return data


def test_linux_script_reports_what_the_board_got_wrong(monkeypatch):
    """hw_linux.boot on a scripted console: the good one passes and fills the memory table; a
    boot line missing, the console's `Linux version` line not the Image's (another build of
    the same size on the card), the files' sizes not the tree's, a command's reply missing,
    the memory probes' output missing (then also the free blocks the text tools' commands wait
    for) each fail; the cleanup leaves the card unmounted."""
    import hw_linux
    import linux_checks

    version = "Linux version 7.3.0 (x@y) (gcc) #28 PREEMPT Tue Nov 10 14:49:15 UTC 2026"
    image = b"\0" * 64 + version.encode() + b"\n" + b"\0" * 64
    dtb = b"D" * 8
    good_line = f"[LINUX] Image {len(image)} B, DTB {len(dtb)} B, starting at 0x80000000"
    console = f"[    0.000000] {version}\r\n" + "".join(f"{ln}\r\n" for ln in linux_checks.BOOT_LINES)
    replies = {cmd: " ".join(want) + "\r\n" for cmd, want in linux_checks.COMMANDS.items()}
    meminfo, buddyinfo = linux_checks.MEM_PROBES
    heap = f"{linux_checks.PYTHON} {linux_checks.MPY_HEAP_PY}"
    # Free blocks of order 6, which the text tools' and MicroPython's launcher's commands wait
    # for, and 7; the heap's growth in mem_info's lines; the REPL's value
    replies |= {
        meminfo: "MemTotal: 7960 kB\r\nMemFree: 3912 kB\r\n",
        buddyinfo: "Node 0, zone Normal 1 0 1 0 0 0 1 1\r\n",
        heap: "GC: total: 63936,\r\nGC: total: 128000,\r\nGC: total: 250368,\r\n" + replies[heap],
        ">>> " + linux_checks.MPY_REPL_LINE: linux_checks.MPY_REPL_OUT + "\r\n",
    }
    mem: dict[str, str] = {}
    times: dict[str, float] = {}

    def failures(linux_line=good_line, console=console, replies=replies):
        mem.clear()
        times.clear()
        port = ShellPort(linux_line, console, replies)
        got = hw_linux.boot(port, 0.0, image, dtb, times, {"image": 64, "bbawk": 9}, mem)
        return got + hw_linux.cleanup(port)

    assert failures() == []
    assert mem["Image"] == "64 B" and mem["MemFree"] == "3,912 kB" and mem["largest free block"] == "512 kB (order 7)"
    # Each of the card's busyboxes' first exec, by its first command
    first = [label for label in times if label.startswith("first exec of ")]
    assert len(first) == len(linux_checks.TOOL_BUSYBOXES), first
    assert "first exec of bbawk (9 B): awk 'BEGIN{print 6*7, sqrt(2)}'" in first, first
    got = failures(replies=replies | {buddyinfo: ""})
    assert got[0].startswith("memory: the probes' output lacks buddyinfo"), got
    # Every command that waits (MicroPython's REPL too) says so
    waits = len(hw_linux.WAIT_LARGE) + sum(map(linux_checks.runs_mpy, linux_checks.COMMANDS)) + 1
    assert len(got) == 1 + waits and all("no /proc/buddyinfo" in g for g in got[1:]), got
    got = failures(console=console.replace("hush - the humble shell", "hush"))
    assert len(got) == 1 and "boot lines missing" in got[0], got
    got = failures(console=console.replace("#28 ", "#27 "))
    assert len(got) == 1 and "not the tree's kernel" in got[0], got
    got = failures(linux_line=good_line.replace(f"Image {len(image)} B", "Image 1 B"))
    assert len(got) == 1 and "not the tree's" in got[0], got
    got = failures(replies=replies | {"uname -a": "Linux (none) 6.0\r\n"})
    assert len(got) == 1 and got[0].startswith("uname -a: missing"), got
    # MicroPython: a traceback, the kernel's failure to allocate MPY's block, the heap grown by
    # more than an order-5 block, the REPL's value missing
    selftest = next(c for c in linux_checks.MPY_COMMANDS if "bc_selftest" in c)
    got = failures(replies=replies | {selftest: replies[selftest] + "Traceback (most recent call last):\r\n"})
    assert len(got) == 1 and "'Traceback' in its output" in got[0], got
    failed = "MPY: page allocation failure: order:7, mode:0xcc0(GFP_KERNEL)\r\n"
    got = failures(replies=replies | {selftest: replies[selftest] + failed})
    assert len(got) == 1 and "'page allocation failure' in its output" in got[0], got
    got = failures(replies=replies | {heap: "GC: total: 63936,\r\nGC: total: 260000,\r\n" + replies[heap]})
    assert len(got) == 1 and "more than 131,072 B at a time" in got[0], got
    got = failures(replies=replies | {">>> " + linux_checks.MPY_REPL_LINE: "3\r\n"})
    assert len(got) == 1 and got[0].startswith("the REPL's 1+1"), got
    assert hw_linux.banner(b"no banner here") is None


def test_linux_script_waits_the_guests_seconds(monkeypatch):
    """hw_linux.wait_large waits RECLAIM_WAIT_S of the guest's uptime for a free block, not of
    the host's clock, which runs ahead of it while the emulator is stopped (here the guest gets
    0.4 s a second): it returns once one is free, says how long the guest waited when none
    came, and without the guest's uptime gives up at RECLAIM_WAIT_WALL_S of the host's."""
    import types

    import hw_linux
    import linux_checks

    order = linux_checks.MPY_ORDER

    def wait(free_after: float | None, uptime: bool = True) -> tuple[str | None, float]:
        host = [0.0]

        def guest() -> float:
            return 100.0 + 0.4 * host[0]

        def run_command(ser, cmd, timeout=0.0):
            host[0] += 1.0
            if cmd == linux_checks.MEM_PROBES[1]:
                n = int(free_after is not None and guest() - 100.0 >= free_after)
                return f"\r\nNode 0, zone Normal 1 0 1 0 0 0 1 {n}\r\n"
            if cmd == linux_checks.UPTIME_PROBE and uptime:
                return f"\r\nup={guest():.2f}\r\n"
            return "\r\n"

        def sleep(s: float) -> None:
            host[0] += s

        monkeypatch.setattr(hw_linux, "run_command", run_command)
        monkeypatch.setattr(hw_linux, "time", types.SimpleNamespace(monotonic=lambda: host[0], sleep=sleep))
        return hw_linux.wait_large(None, "MPY", order), host[0]

    got, host = wait(free_after=linux_checks.RECLAIM_WAIT_S - 1)
    assert got is None and host > linux_checks.RECLAIM_WAIT_S, (got, host)
    got, host = wait(free_after=None)
    want = f"no free block of order {order} within "
    assert got and want in got and "s of the guest's time" in got, got
    waited = float(got.split(want)[1].split()[0])
    assert linux_checks.RECLAIM_WAIT_S <= waited < linux_checks.RECLAIM_WAIT_S + 2, got
    got, host = wait(free_after=None, uptime=False)
    assert got and "within 0.0 s" in got and host >= linux_checks.RECLAIM_WAIT_WALL_S, (got, host)


class ViPort:
    """A port that answers hw_linux.vi as the shell and a vi that only records its keys would:
    `vi FILE` with vi's status line, its keys up to `:wq` CR, then the prompt; `cat FILE` with
    the text the session given by texts left (the next of texts each time), other lines with
    their echo (each write's as it comes) and the prompt. A free block of order 6 in
    /proc/buddyinfo."""

    def __init__(self, texts: list[str]):
        import linux_checks

        self.lc = linux_checks
        self.texts = texts
        self.out = b""
        self.typed = b""
        self.keys: list[bytes] = []  # each vi's keys
        self.lines: list[str] = []  # the shell's command lines, in order
        self.in_vi = False

    def write(self, data: bytes):
        lc = self.lc
        if self.in_vi:
            self.keys[-1] += data
            if self.keys[-1].endswith(b":wq\r") or self.keys[-1].endswith(b":q!\r"):
                self.in_vi = False
                self.out += f"\r\n{lc.PROMPT}".encode()
            return
        self.typed += data
        self.out += data.rstrip(b"\n")
        if not self.typed.endswith(b"\n"):
            return
        line, self.typed = self.typed.strip().decode(), b""
        self.lines.append(line)
        self.out += b"\r\n"
        if line == f"vi {lc.VI_FILE}":
            self.in_vi = True
            self.keys.append(b"")
            self.out += f"\x1b[24;1H{lc.ViSession.ready}1/1 100%".encode()
            return
        if line == f"cat {lc.VI_FILE}":
            self.out += self.texts.pop(0).replace("\n", "\r\n").encode()
        elif line == lc.MEM_PROBES[1]:
            self.out += b"Node 0, zone Normal 1 0 1 0 0 0 1\r\n"
        self.out += lc.PROMPT.encode()

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data, self.out = self.out[:n], self.out[n:]
        return data


def test_linux_script_drives_vi(monkeypatch):
    """hw_linux.vi: each ViSession's keys reach vi as they are (a split step's parts in one
    write without split), and cat's text must be the session's; a wrong text fails, with the
    USB bridge's timing named for the session that splits a step, and the sessions after it
    are skipped. Each exec waits for a large block first: rm (VI_CLEAR) once, then each vi."""
    import hw_linux
    import linux_checks

    monkeypatch.setattr(linux_checks, "VI_QUIET_S", 0.01)
    sessions = linux_checks.VI_SESSIONS
    keys = ["".join("".join(s) if isinstance(s, tuple) else s for s in v.steps).encode() for v in sessions]
    probe = linux_checks.MEM_PROBES[1]
    vi_file = linux_checks.VI_FILE
    for split in (True, False):
        port = ViPort([v.text for v in sessions])
        times: dict[str, float] = {}
        assert hw_linux.vi(port, times, split=split) == []
        assert port.keys == keys
        each = [probe, f"vi {vi_file}", probe, f"cat {vi_file}"]
        assert port.lines == [probe, linux_checks.VI_CLEAR] + each * len(sessions), port.lines
        assert f"vi {sessions[-1].name}: its keys to the prompt" in times
    port = ViPort([sessions[0].text, "one\n", "x\n"])
    got = hw_linux.vi(port, {})
    assert len(got) == 1 and got[0].startswith(f"vi {sessions[1].name}: ") and "bridge" not in got[0], got
    assert len(port.keys) == 2
    port = ViPort([v.text for v in sessions[:-1]] + ["one\n"])
    got = hw_linux.vi(port, {})
    assert len(got) == 1 and got[0].startswith(f"vi {sessions[-1].name}: ") and "USB bridge" in got[0], got


class EchoPort:
    """A shell's line editing that echoes what it has read, from the host's view: each read
    returns the echo of what was written before it (none with mute). in_flight is the most
    bytes the host had written and not yet read back as echo when it wrote; lines the lines."""

    def __init__(self, mute: bool = False):
        self.mute = mute
        self.out = b""
        self.typed = b""
        self.unechoed = 0
        self.in_flight = 0
        self.lines: list[str] = []

    def write(self, data: bytes):
        self.unechoed += len(data)
        self.in_flight = max(self.in_flight, self.unechoed)
        self.typed += data
        if not self.mute:
            self.out += data.replace(b"\n", b"\r\n")
        while b"\n" in self.typed:
            line, self.typed = self.typed.split(b"\n", 1)
            self.lines.append(line.decode())

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data, self.out = self.out[:n], self.out[n:]
        self.unechoed = len(self.out) if not self.mute else self.unechoed
        return data


def test_linux_script_paces_a_line_by_its_echo(monkeypatch):
    """hw_linux.send_line: a command line's next chunk goes once the console has echoed the
    one before, so no more than a chunk is ever on its way (the UART's 32-byte RX FIFO,
    whatever the host's or the emulator's timing), and what it read comes back for the
    caller; no echo is a TimeoutError. type_line (after the paste) doesn't wait for one."""
    import hw_linux
    import linux_checks

    monkeypatch.setattr(hw_linux, "SEND_GAP_S", 0.0)
    for line in (linux_checks.MEM_PROBES[1], "x" * 7, "x" * 8, "x" * 15, ""):
        port = EchoPort()
        text = hw_linux.send_line(port, line)
        assert port.lines == [line] and port.in_flight <= hw_linux.SEND_CHUNK, (line, port.in_flight)
        assert text + port.out.decode() == line + "\r\n"  # nothing read is lost to the caller
    monkeypatch.setattr(hw_linux, "ECHO_TIMEOUT", 0.05)
    port = EchoPort(mute=True)
    with pytest.raises(TimeoutError, match="no echo of 'exec 3</'"):
        hw_linux.send_line(port, linux_checks.MEM_PROBES[1])
    assert port.in_flight == hw_linux.SEND_CHUNK
    port = EchoPort(mute=True)
    hw_linux.type_line(port, linux_checks.PASTE_DONE)
    assert port.lines == [linux_checks.PASTE_DONE]


class LostEchoPort(EchoPort):
    """A shell whose echo of the first chunk written is lost (a FIFO overrun's dropped bytes):
    the rest is echoed, and ^C drops the line and prints the prompt."""

    def __init__(self):
        super().__init__()
        self.cancelled = 0

    def write(self, data: bytes):
        import hw_linux

        if data == hw_linux.CANCEL_LINE:
            self.cancelled += 1
            self.typed, self.out = b"", self.out + b"^C\r\n/ # "
            return
        if not self.typed and not self.lines:
            self.typed += data  # no echo
            return
        super().write(data)


def test_linux_script_goes_on_without_an_echo(monkeypatch, capsys):
    """hw_linux.run_command: a chunk's echo missing fails that command alone (None), the line
    dropped with ^C at the shell's prompt, so the run goes on to its next command."""
    import hw_linux
    import linux_checks

    monkeypatch.setattr(hw_linux, "ECHO_TIMEOUT", 0.05)
    monkeypatch.setattr(hw_linux, "COMMAND_TIMEOUT", 0.2)
    port = LostEchoPort()
    assert hw_linux.run_command(port, linux_checks.MEM_PROBES[1]) is None
    assert port.cancelled == 1 and port.out == b""  # read up to the prompt after ^C
    assert "[WARN] no echo of" in capsys.readouterr().out


class SplicePort:
    """A port whose console gets a kernel line (KERNEL_MSG) spliced in at byte `at` of what it
    sends, as a printk lands in the middle of a line's echo or a reply."""

    KERNEL_MSG = b"[  255.548990] random: crng init done\r\n"

    def __init__(self, inner, at: int):
        self.inner, self.at, self.sent = inner, at, 0

    def write(self, data: bytes):
        self.inner.write(data)

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data = self.inner.read(n)
        k = self.at - self.sent
        if 0 <= k <= len(data) and data:
            data = data[:k] + self.KERNEL_MSG + data[k:]
        self.sent += len(data)
        return data


def test_linux_script_skips_kernel_lines(monkeypatch, capsys):
    """A kernel line on the console (random's `crng init done`, CI run 43701558484) anywhere in
    a command line's echo or its reply: send_line still finds each chunk's echo, run_command
    returns the reply without it and prints it."""
    import hw_linux
    import linux_checks

    monkeypatch.setattr(hw_linux, "ECHO_TIMEOUT", 0.2)
    monkeypatch.setattr(hw_linux, "COMMAND_TIMEOUT", 0.2)
    probe = linux_checks.MEM_PROBES[1]
    replies = {probe: "Node 0, zone Normal 1 0 1 0 0 0 1\r\n"}
    want = "\r\n" + replies[probe] + linux_checks.PROMPT
    ats = range(len(probe) + len(want))
    for at in ats:
        shell = ShellPort("", "", replies)
        shell.out, shell.shell = b"", True
        assert hw_linux.run_command(SplicePort(shell, at), probe) == want, at
    assert capsys.readouterr().out.count("random: crng init done") == len(ats)


class PastePort:
    """A shell that runs `echo WORD` lines (the backslashes gone) and loses the bytes of every
    write longer than keep (the RX FIFO overrun of a paste sent at once)."""

    def __init__(self, keep: int):
        self.keep = keep
        self.out = b""
        self.typed = b""

    def write(self, data: bytes):
        self.typed += data[: self.keep]
        while b"\n" in self.typed:
            line, self.typed = self.typed.split(b"\n", 1)
            text = line.decode()
            self.out += f"{text}\r\n".encode()
            if text.startswith("echo "):
                self.out += f"{text[5:].replace(chr(92), '')}\r\n".encode()
            self.out += b"/ # "

    def flush(self):
        pass

    def read(self, n: int) -> bytes:
        data, self.out = self.out[:n], self.out[n:]
        return data


def test_linux_script_pastes(monkeypatch):
    """hw_linux.pastes: the paste in one write is only recorded, however many of its lines a
    lost tail cuts off; the monitor's pacing must pass every line."""
    import hw_linux
    import linux_checks

    monkeypatch.setattr(hw_linux, "PASTE_SETTLE_S", 0.0)
    # The monitor's chunks 1 ms apart (Pacer's default gap is bound when vux_tool loads, so
    # the class itself is replaced)
    pacer = test_hardware.vux_tool.Pacer
    monkeypatch.setattr(test_hardware.vux_tool, "Pacer", lambda: pacer(gap=0.001))
    n = len(linux_checks.PASTE_LINES)
    pasted: dict[str, str] = {}
    assert hw_linux.pastes(PastePort(keep=64), pasted) == []
    assert list(pasted.values()) == [f"2 of {n} lines whole", f"{n} of {n} lines whole"], pasted
    pasted.clear()
    got = hw_linux.pastes(PastePort(keep=8), pasted)
    assert got == [f"the paced paste: 0 of {n} lines whole"], got


def test_bitstream_load_errors(tmp_path):
    assert test_hardware.load_bitstream(None, str(tmp_path / "pack.fs")) == "no openFPGALoader (--loader)"
    assert "missing" in test_hardware.load_bitstream("openFPGALoader", str(tmp_path / "pack.fs"))


EMU = os.environ.get("VUX9K_EMU_BIN") or os.path.join(REPO_ROOT, "build", "emu", "target", "release", "vux9k-emu")


@pytest.mark.skipif(
    os.environ.get("VUX9K_HW_SUITE_EMU") != "1", reason="make sim-hw-suite-emu runs it (~3 min, test-slow)"
)
def test_suite_on_the_emulator(tmp_path):
    img = tmp_path / "sd.img"
    img.write_bytes(test_hardware.vux_tool.build_fat32_image({}))  # no \VUX9K\: install makes it
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    emu = subprocess.Popen(
        [EMU, "--tcp", str(port), "--sd", str(img)], cwd=REPO_ROOT, stderr=subprocess.PIPE, text=True
    )
    # The emulator's stderr is read to its end in a thread (a full pipe would stop it);
    # the test waits for the listening line at most 30 s
    listening = threading.Event()
    log: list = []

    def drain():
        assert emu.stderr
        for line in emu.stderr:
            log.append(line)
            if "waiting for a UART client" in line:
                listening.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        assert listening.wait(30), f"the emulator did not listen: {''.join(log)[-2000:]}"
        r = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "scripts", "test_hardware.py"),
                "--port",
                f"socket://127.0.0.1:{port}",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        emu.kill()
        emu.wait()
    assert r.returncode == 0, r.stdout[-3000:]
    assert "Score       : 13/13" in r.stdout and "(and 3 skipped)" in r.stdout, r.stdout[-2000:]
