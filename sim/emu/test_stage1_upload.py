# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The two-stage boot through the real tools/vux_tool.py, in emulated time (EmuClock,
EmuSerial): getting to the first stage's prompt, a RAM boot of the second stage and its
refusals, installing a second stage (STAGE2.BIN, the previous one kept as STAGE2.OLD)
and the first stage falling back to STAGE2.OLD. The emulator side of
sim/integration/test_soc_hardware_flow.py.
"""

import pytest
from s1_env import BANNER, FAT_LBA, S2_NAME, STAGE1_VERSION, STAGE2_BASE, STAGE2_VERSION, card, handoff, stage2, start
from vux9k import EmuClock, EmuSerial, vux_tool

BIN = "VUX9K/STAGE2.BIN"


@pytest.fixture
def tool(monkeypatch):
    def make(files=None, **kw):
        soc = start(card(files if files is not None else {BIN: stage2()}), **kw)
        monkeypatch.setattr(vux_tool, "time", EmuClock(soc))
        return soc, EmuSerial(soc, timeout=0.05)

    return make


def test_enter_s1_from_power_on_and_from_stage2(tool):
    soc, ser = tool()
    assert "[SAFE MODE] Key received" in vux_tool.enter_s1(ser)
    # Now at the second stage: it is soft-reset back
    out = vux_tool.ram_boot(ser, stage2())
    assert "[S2]" in out
    out = vux_tool.enter_s1(ser)
    assert "[RESET] Soft reset" in out and BANNER in out


def test_ram_boot(tool):
    soc, ser = tool({})  # nothing on the card
    vux_tool.enter_s1(ser)
    out = vux_tool.ram_boot(ser, stage2(version=7))
    assert out.startswith("[OK] ")
    assert f"[BOOT] Starting at 0x{STAGE2_BASE:08X}" in out
    assert handoff(out) == (1, FAT_LBA, STAGE1_VERSION)


def test_ram_boot_without_a_card(tool):
    soc, ser = tool(card_present=False)
    vux_tool.enter_s1(ser, timeout=30.0)
    out = vux_tool.ram_boot(ser, stage2(), timeout=30.0)
    assert handoff(out) == (0, 0, STAGE1_VERSION)


@pytest.mark.parametrize(
    "file, reason",
    [
        (stage2(magic_override=0x12345678), "not a VUX9 v4 file"),
        (stage2(load_addr=0x8000_0000), "loads outside 80700000-807FFFFF"),
        (stage2(mode="hack"), "not RV32, mode 0"),
        (stage2(crc_override=0), "CRC mismatch"),
        (stage2(entry_point=STAGE2_BASE + 2), "bad entry point 80700002"),
    ],
    ids=["magic", "window", "hack", "crc", "entry"],
)
def test_ram_boot_refusals(tool, file, reason):
    soc, ser = tool()
    vux_tool.enter_s1(ser)
    with pytest.raises(vux_tool.UploadError, match=f"\\[BOOT\\] {reason}"):
        vux_tool.ram_boot(ser, file)
    # Still at the prompt, which works
    assert "[SD] SDHC card" in vux_tool.send_cmd_and_wait(ser, "i", prompt="s1> ")


def test_install_keeps_the_previous_as_old(tool):
    soc, ser = tool()
    vux_tool.enter_s1(ser)
    vux_tool.ram_boot(ser, stage2(version=2))
    assert vux_tool.install_stage2(ser, stage2(version=2)) == "[INSTALL] OK"
    assert vux_tool.install_stage2(ser, stage2(version=3)) == "[INSTALL] OK"
    # Back to the first stage, which boots the new one
    ser.write(b"reset\r")
    got, out = vux_tool.read_until(ser, ("s2> ",), 5.0)
    assert got and f'[BOOT] STAGE2.BIN v3 "{S2_NAME}"' in out, out
    vux_tool.enter_s1(ser)
    out = vux_tool.send_cmd_and_wait(ser, "i", prompt="s1> ")
    assert "STAGE2.BIN: v3 " in out and "STAGE2.OLD: v2 " in out, out


def test_install_refuses_a_bad_file(tool):
    soc, ser = tool()
    vux_tool.enter_s1(ser)
    vux_tool.ram_boot(ser, stage2())
    with pytest.raises(vux_tool.UploadError, match=r"\[INSTALL\] CRC mismatch"):
        vux_tool.install_stage2(ser, stage2(crc_override=1))
    with pytest.raises(vux_tool.UploadError, match=r"\[INSTALL\] loads outside"):
        vux_tool.install_stage2(ser, stage2(load_addr=0x8000_0000))


def test_install_makes_the_directory(tool):
    soc, ser = tool({"README.TXT": b"x"})
    vux_tool.enter_s1(ser)
    vux_tool.ram_boot(ser, stage2())
    assert vux_tool.install_stage2(ser, stage2(version=6)) == "[INSTALL] OK"
    ser.write(b"reset\r")
    got, out = vux_tool.read_until(ser, ("s2> ",), 5.0)
    assert got and "[BOOT] STAGE2.BIN v6 " in out, out


def test_broken_bin_boots_old(tool):
    soc, ser = tool()
    vux_tool.enter_s1(ser)
    vux_tool.ram_boot(ser, stage2())
    vux_tool.install_stage2(ser, stage2(version=4))  # the RAM-booted one becomes .OLD
    assert vux_tool.install_stage2(ser, stage2(version=5, crc_override=0), unchecked=True) == "[UNCHECKED] OK"
    ser.write(b"reset\r")
    got, out = vux_tool.read_until(ser, ("s2> ",), 5.0)
    assert got and f"[BOOT] STAGE2.OLD (STAGE2.BIN missing or broken) v{STAGE2_VERSION} " in out, out


def test_ram_boot_transfer_errors(tool):
    """What the first stage's `u` says when the host breaks transfer v2"""
    soc, ser = tool()
    vux_tool.enter_s1(ser)
    good = stage2()

    def attempt(length=None, data=b"", blocks=False) -> str:
        """`u`, the length, data (all at once, or each block after its [READY-BLK]), no
        CRC; the first stage's answer up to its prompt."""
        ser.write(b"u")
        vux_tool.read_until(ser, ("[READY-V2]",), 1.0)
        if length is not None:
            ser.write(length.to_bytes(4, "little"))
        if blocks:
            for i in range(0, len(data), 512):
                vux_tool.read_until(ser, (f"[READY-BLK:{i // 512}]",), 1.0)
                ser.write(data[i : i + 512])
        else:
            ser.write(data)
        got, out = vux_tool.read_until(ser, ("s1> ",), 12.0)  # past the 5 s byte timeout
        assert got, out
        return out

    assert "[ERR] Length timeout" in attempt()
    assert "[ERR] Bad length: 1048641" in attempt(1048641)
    out = attempt(64)
    assert "[ERR] Cannot store 64 bytes" in out and "[BOOT] not a VUX9 v4 file" in out
    assert "[ERR] Timeout at block 0, byte 100" in attempt(len(good), good[:100])
    assert "[ERR] CRC timeout" in attempt(len(good), good, blocks=True)
    ser.write(b"u")
    with pytest.raises(vux_tool.UploadError, match="CRC mismatch: sent 00000001"):
        vux_tool.send_upload_v2(ser, good, crc_override=1)
