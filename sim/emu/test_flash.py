# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Flashing through the real tools/vux_tool.py (flash_slot, inspect_slot, boot_slot)
against the Boot Manager on the emulator, in emulated time: vux_tool's clock is the
SoC's (EmuClock) and its port is the SoC's UART (EmuSerial). The emulator side of
sim/integration/test_soc_hardware_flow.py.
"""

import pytest
from bm_env import HACK_BIN, HASH_PAYLOAD, boot
from vux9k import EmuClock, EmuSerial, slot_lba, vux_tool


@pytest.fixture
def tool(monkeypatch):
    soc, _ = boot()
    monkeypatch.setattr(vux_tool, "time", EmuClock(soc))
    return soc, EmuSerial(soc)


def sd_matches(soc, raw: bytes, slot: int) -> bool:
    return all(soc.sd_sector(slot_lba(slot) + i) == raw[i * 512 : (i + 1) * 512] for i in range(len(raw) // 512))


def test_flash_and_boot_riscv(tool):
    soc, ser = tool
    meta = vux_tool.flash_slot(ser, HASH_PAYLOAD, slot=3, name="Flashed", mode="riscv")
    raw, _ = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=3, name="Flashed", mode="riscv")
    assert sd_matches(soc, raw, 3)
    out = vux_tool.inspect_slot(ser, slot=3)
    assert f"CRC32: 0x{meta['crc32']:08X}" in out, out
    out = vux_tool.boot_slot(ser, slot=3, timeout=2.0)
    assert "[RL] Slot 3" in out
    ser.timeout = 0.5
    assert b"#" in ser.read(64) or "#" in out


def test_flash_and_boot_hack_demo(tool):
    soc, ser = tool
    with open(HACK_BIN, "rb") as f:
        payload = f.read()
    vux_tool.flash_slot(ser, payload, slot=2, name="HackDemo", mode="hack")
    raw, _ = vux_tool.build_vux9_image(payload, slot=2, name="HackDemo", mode="hack")
    assert sd_matches(soc, raw, 2)
    out = vux_tool.boot_slot(ser, slot=2, timeout=1.0).encode()  # may hold the whole report
    ser.timeout = 3.0
    while b"(100%)!" not in out:
        chunk = ser.read(256)
        assert chunk, f"Hack demo stopped: {out[-200:]!r}"
        out += chunk
    assert not soc.riscv_mode, "the RL passed Hack as the boot mode"


# The 'w' count is one raw byte: 10 and 13 are LF and CR
@pytest.mark.parametrize("sectors", [9, 10, 11, 13])
def test_flash_every_sector_count(tool, sectors):
    soc, ser = tool
    payload = HASH_PAYLOAD + bytes(sectors * 512 - 64 - len(HASH_PAYLOAD))
    meta = vux_tool.flash_slot(ser, payload, slot=4, name="Count", mode="riscv")
    assert meta["num_sectors"] == sectors
    raw, _ = vux_tool.build_vux9_image(payload, slot=4, name="Count", mode="riscv")
    assert sd_matches(soc, raw, 4)
