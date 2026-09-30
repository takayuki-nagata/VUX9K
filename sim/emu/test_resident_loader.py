# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Resident Loader error paths on the emulator. It reads a slot twice: first without
touching I-RAM (E1 slot sector unreadable, E2 bad header, E3 bad size, E4 a later
sector unreadable, E5 CRC mismatch), each falling back to the Boot Manager, which must
come back to its prompt; then into I-RAM, where a failure (E6) leaves no Boot Manager
to return to and stops.
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


def test_e4_unreadable_later_sector_returns_to_a_working_boot_manager():
    # The first pass finds it before anything is written over the Boot Manager
    payload = HASH_PAYLOAD + bytes(1000)
    slot1, meta = slot_image(payload, slot=1, mode="riscv")
    assert meta["num_sectors"] >= 2
    soc, _ = boot(sd_sectors=slot1, sd_card={"bad_sectors": [slot_lba(1) + 1]})
    assert "[RL] E4" in launch(soc)[1]
    assert "[CMD:0x6C]" in cmd(soc, "l")  # and it still works


def test_e5_crc_mismatch_returns_to_the_boot_manager():
    raw, meta = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=1, mode="riscv", crc_override=0xDEADBEEF)
    soc, _ = boot(sd_sectors={slot_lba(1): raw[:512]})
    assert "[RL] E5" in launch(soc)[1]


def test_e6_second_read_fails_and_stops():
    # Sector 2 of the slot reads once (first pass), then fails (second pass): the Boot
    # Manager is already half overwritten, so the RL stops instead of returning
    payload = HASH_PAYLOAD + bytes(1000)
    slot1, _ = slot_image(payload, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"fail_after": [(slot_lba(1) + 1, 1)]})
    mark = len(soc.uart_received())
    cmd(soc, "1", until=b"[RL] Slot")
    out = wait_for(soc, b"[RL] E6: reload the bitstream\n", 60_000_000, start=mark)
    assert "Load failed" not in out
    end = len(soc.uart_received())
    soc.run(BOOT_CYCLES)
    assert len(soc.uart_received()) == end, "stopped: no Boot Manager banner"


def test_reboot_after_swapping_to_an_sdsc_card():
    # 'r' on an SDSC card used to send mailbox 0, which the RL took for "an app returns
    # to slot 0" and read with the card type an earlier launch had left behind
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
