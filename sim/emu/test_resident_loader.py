# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Resident Loader error paths on the emulator: E1 (slot sector unreadable), E2 (bad
header), E3 (bad size), E4 (a later sector unreadable). Each falls back to the
Boot Manager, which must come back to its prompt.
"""

import pytest
from bm_env import BANNER, BOOT_CYCLES, HASH_PAYLOAD, PROMPT, boot, cmd
from vux9k import sd_image, slot_image, slot_lba, vux_tool, wait_for


def launch(soc, slot=1, back_to_prompt=True):
    resp = cmd(soc, str(slot), until=b"[RL] Slot")
    mark = len(soc.uart_received())
    # A bad sector costs the RL its full data-token wait (50,000 bytes, ~27M cycles)
    out = wait_for(soc, b"Load failed, returning to Boot Manager", 60_000_000, start=mark)
    if back_to_prompt:
        wait_for(soc, PROMPT, BOOT_CYCLES, start=len(soc.uart_received()))
    return soc, resp + out


def test_e1_unreadable_header_sector():
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"bad_sectors": [slot_lba(1)]})
    assert "[RL] E1" in launch(soc)[1]


def test_e2_bad_magic():
    raw, meta = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=1, mode="riscv", magic_override=0x12345678)
    soc, _ = boot(sd_sectors={slot_lba(1): raw[:512]})
    assert "[RL] E2" in launch(soc)[1]


def test_e3_too_big_for_i_ram():
    payload = HASH_PAYLOAD + bytes(14336)  # > 14 KB
    raw, meta = vux_tool.build_vux9_image(payload, slot=1, mode="riscv")
    sectors = {slot_lba(1) + i: raw[i * 512 : (i + 1) * 512] for i in range(meta["num_sectors"])}
    soc, _ = boot(sd_sectors=sectors)
    assert "[RL] E3" in launch(soc)[1]


def test_e4_unreadable_later_sector():
    payload = HASH_PAYLOAD + bytes(1000)
    slot1, meta = slot_image(payload, slot=1, mode="riscv")
    assert meta["num_sectors"] >= 2
    soc, _ = boot(sd_sectors=slot1, sd_card={"bad_sectors": [slot_lba(1) + 1]})
    assert "[RL] E4" in launch(soc, back_to_prompt=False)[1]


@pytest.mark.xfail(
    strict=True,
    reason="firmware bug (step 6): on E4 the RL has already copied the slot's first sector "
    "over the Boot Manager in lower I-RAM, then soft-resets into it",
)
def test_e4_returns_to_a_working_boot_manager():
    payload = HASH_PAYLOAD + bytes(1000)
    slot1, _ = slot_image(payload, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"bad_sectors": [slot_lba(1) + 1]})
    launch(soc)


@pytest.mark.xfail(strict=True, reason="firmware bug (step 6): the RL doesn't check the payload's CRC32")
def test_e5_crc_mismatch_returns_to_the_boot_manager():
    raw, meta = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=1, mode="riscv", crc_override=0xDEADBEEF)
    soc, _ = boot(sd_sectors={slot_lba(1): raw[:512]})
    assert "[RL] E5" in launch(soc)[1]


@pytest.mark.xfail(
    strict=True,
    reason="firmware bug (step 6): 'r' on an SDSC card sends mailbox 0, which the RL takes for "
    "'an app returns to slot 0' and reads with a stale card type from an earlier launch",
)
def test_reboot_after_swapping_to_an_sdsc_card():
    # A launch on an SDHC card leaves the RL's saved card type at SDHC (E2: nothing to load)
    raw, _ = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=1, mode="riscv", magic_override=0x12345678)
    soc, _ = boot(sd_sectors={slot_lba(1): raw[:512]})
    assert "[RL] E2" in launch(soc)[1]
    # Swap in an SDSC card whose slot 0 prints '#', and have the Boot Manager find it
    slot0, _ = slot_image(HASH_PAYLOAD, slot=0, mode="riscv")
    soc.sd_remove()
    soc.sd_insert(sd_image(slot0), sdhc=False, strict=True)  # strict: no guessing the addressing
    cmd(soc, "i", timeout_cycles=BOOT_CYCLES)
    mark = len(soc.uart_received())
    cmd(soc, "r", until=b"[RL] Slot 0")
    wait_for(soc, b"#", 60_000_000, start=mark)


@pytest.mark.parametrize("sdhc", [True, False], ids=["sdhc", "sdsc"])
def test_slot_boot_on_both_card_types(sdhc):
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"sdhc": sdhc, "strict": True})
    mark = len(soc.uart_received())
    cmd(soc, "1", until=b"[RL] Slot 1")
    wait_for(soc, b"#", 5_000_000, start=mark)
    assert not [v for v in soc.sd_violations if not v.startswith("power-up")], soc.sd_violations


def test_reboot_reloads_the_boot_manager():
    soc, _ = boot()
    cmd(soc, "r", until=BANNER.encode())
    wait_for(soc, PROMPT, BOOT_CYCLES, start=len(soc.uart_received()))
    assert soc.riscv_mode
