# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Boot Manager self-update from SD slot 0 on the emulator: a newer, intact image is
installed through the Resident Loader; every check that rejects one (CRC, size,
version, entry instruction, unreadable sector) is exercised, and S2 held at power-on
(SAFE MODE) skips the check.
"""

import pytest
from bm_env import BM_BIN, BOOT_CYCLES, BOOT_MGR_VERSION, boot
from vux9k import slot_lba, start_soc, vux_tool, wait_for


def slot0(payload, **kw):
    raw, meta = vux_tool.build_vux9_image(payload, slot=0, name="BootMgr", mode="riscv", **kw)
    return {slot_lba(0) + i: raw[i * 512 : (i + 1) * 512] for i in range(meta["num_sectors"])}, meta


@pytest.fixture(scope="module")
def bm_bin():
    with open(BM_BIN, "rb") as f:
        return f.read()


def test_valid_update_is_installed(bm_bin):
    sectors, meta = slot0(bm_bin, version=BOOT_MGR_VERSION + 1)
    soc, out = boot(sd_sectors=sectors)
    assert f"[UPDATE] Verified valid Boot Manager update (v{BOOT_MGR_VERSION + 1}" in out, out
    assert "[UPDATE] Booted newly updated Boot Manager!" in out, out


def test_same_version_is_ignored(bm_bin):
    sectors, _ = slot0(bm_bin, version=BOOT_MGR_VERSION)
    soc, out = boot(sd_sectors=sectors)
    assert "[UPDATE]" not in out


def test_corrupt_crc_is_rejected(bm_bin):
    sectors, _ = slot0(bm_bin, version=BOOT_MGR_VERSION + 1, crc_override=0xDEADBEEF)
    soc, out = boot(sd_sectors=sectors)
    assert "[UPDATE] Slot 0 CRC32 mismatch" in out and "Bypassing" in out, out


def test_oversized_image_is_rejected():
    sectors, _ = slot0(bytes([0x13, 0, 0, 0]) * 3600, version=BOOT_MGR_VERSION + 1)
    soc, out = boot(sd_sectors=sectors)
    assert "[UPDATE] Slot 0 image size invalid (14400 bytes > 14KB limit)" in out, out


def test_zero_entry_instruction_is_rejected():
    sectors, _ = slot0(bytes(64), version=BOOT_MGR_VERSION + 1)
    soc, out = boot(sd_sectors=sectors)
    assert "[UPDATE] Slot 0 entry instruction invalid (0x00000000)" in out, out


def test_unreadable_sector_is_rejected(bm_bin):
    sectors, _ = slot0(bm_bin, version=BOOT_MGR_VERSION + 1)
    soc, out = boot(sd_sectors=sectors, sd_card={"bad_sectors": [slot_lba(0) + 2]})
    assert f"[UPDATE] Error reading Slot 0 sector {slot_lba(0) + 2}. Bypassing update." in out, out


def test_safe_mode_skips_the_update(bm_bin):
    sectors, _ = slot0(bm_bin, version=BOOT_MGR_VERSION + 1)
    soc = start_soc(sd_sectors=sectors)
    soc.set_button(True)  # held from power-on
    wait_for(soc, b"[SAFE MODE] Button S2 held. Bypassing Slot 0 auto-update.", BOOT_CYCLES)
    soc.set_button(False)
    out = wait_for(soc, b"vux> ", BOOT_CYCLES)
    assert "[UPDATE]" not in out
