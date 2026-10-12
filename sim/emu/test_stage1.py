# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The first stage (firmware/stage1) on the emulator: booting \\VUX9K\\STAGE2.BIN from a
FAT32 card, the fallback to STAGE2.OLD, staying at the prompt (S2 held, a key), its
commands, what keeps a second stage from starting, card types, and the soft reset back.
"""

from s1_env import (
    BANNER,
    BOOT_CYCLES,
    FAT_LBA,
    S1_PROMPT,
    S2_PROMPT,
    STAGE1_VERSION,
    STAGE2_BASE,
    boot,
    card,
    cmd,
    entry_at,
    fixture,
    handoff,
    stage2,
    start,
    to_prompt,
)
from vux9k import UART_BIT, ms, wait_for

BIN = "VUX9K/STAGE2.BIN"
OLD = "VUX9K/STAGE2.OLD"


def broken(data: bytes) -> bytes:
    """data with one payload byte flipped (the header's CRC no longer matches)."""
    b = bytearray(data)
    b[100] ^= 1
    return bytes(b)


def test_boots_stage2_from_the_card():
    soc, out = boot()
    assert BANNER in out
    assert '[BOOT] STAGE2.BIN v1 "VUX9K stage 2 (provisional)" ' in out, out
    assert f"[BOOT] Starting at 0x{STAGE2_BASE:08X}" in out
    assert "VUX9K Stage 2 (provisional)" in out
    # SDHC, the partition at LBA 2048, the first stage's version
    assert handoff(out) == (1, FAT_LBA, STAGE1_VERSION)
    assert soc.pc >= STAGE2_BASE


def test_a_key_after_the_banner_keeps_the_prompt():
    soc = start(files={BIN: stage2()})
    out = to_prompt(soc)
    assert "[SAFE MODE] Key received: staying at the prompt." in out
    assert "[BOOT]" not in out
    assert "[b] Boot \\VUX9K\\STAGE2.BIN" in out
    # and later on, b boots
    out = cmd(soc, "b", until=S2_PROMPT)
    assert handoff(out) == (1, FAT_LBA, STAGE1_VERSION)


def test_s2_held_keeps_the_prompt():
    soc = start(files={BIN: stage2()}, button=True)
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert "[SAFE MODE] Button S2 held: staying at the prompt." in out


def test_prompt_commands():
    soc = start(files={BIN: stage2()})
    to_prompt(soc)
    assert "[r] Soft reset" in cmd(soc, "h")
    assert "[i] SD card" in cmd(soc, "?")
    assert "Unknown command" in cmd(soc, "z")
    # Enter redraws the prompt; a line feed or NUL alone does nothing
    mark = len(soc.uart_received())
    soc.uart_send(b"\n\x00\r")
    assert wait_for(soc, S1_PROMPT, ms(5), start=mark) == "\ns1> "


def test_falls_back_to_old():
    soc, out = boot(files={BIN: broken(stage2(version=2)), OLD: stage2(version=1)})
    assert "[BOOT] STAGE2.OLD (STAGE2.BIN missing or broken) v1 " in out, out
    soc, out = boot(files={OLD: stage2(version=1)})
    assert "[BOOT] STAGE2.OLD (STAGE2.BIN missing or broken) v1 " in out, out


def test_reports_why_nothing_starts():
    cases = [
        ({BIN: broken(stage2())}, "STAGE2.BIN: CRC mismatch; STAGE2.OLD: not found"),
        ({"OTHER.TXT": b"x"}, "STAGE2.BIN: not found; STAGE2.OLD: not found"),
        ({"VUX9K": b"a file"}, "STAGE2.BIN: not a file; STAGE2.OLD: not a file"),
        ({BIN: b"not v4" * 20}, "STAGE2.BIN: not a VUX9 v4 file; STAGE2.OLD: not found"),
        (
            {BIN: stage2(load_addr=0x8000_0000)},
            "STAGE2.BIN: loads outside 80700000-807FFFFF; STAGE2.OLD: not found",
        ),
    ]
    for files, want in cases:
        soc = start(files=files)
        out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
        assert f"[BOOT] {want}\n" in out, out


def test_starts_rv32_only():
    hack = stage2(mode="hack", load_addr=STAGE2_BASE)
    soc = start(files={BIN: hack})
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert "[BOOT] STAGE2.BIN v1 " in out
    assert "[BOOT] not RV32, mode 0\n" in out, out


def test_cards_without_a_fat32_volume():
    img = bytearray(card({BIN: stage2()}))
    no_sig = bytearray(len(img))
    fat16 = bytearray(img)
    fat16[446 + 4] = 0x06
    empty = bytearray(img)
    empty[446 : 446 + 16] = bytes(16)
    cases = [
        (no_sig, "no 55AA signature (MBR or boot sector)"),
        (fat16, "not FAT32 (reformat the card as FAT32)"),
        (empty, "no partition"),
    ]
    for image, want in cases:
        soc = start(bytes(image))
        out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
        assert f"[BOOT] {want}\n" in out, out
    soc = start(card({BIN: stage2()}), sd_card={"bad_sectors": [FAT_LBA]})
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert f"[BOOT] card error at LBA {FAT_LBA}\n" in out, out


def test_no_card():
    soc = start(card_present=False)
    out = wait_for(soc, S1_PROMPT, 8 * BOOT_CYCLES)
    assert "[BOOT] No SD card\n" in out
    assert "[SD] No card / init failed" in cmd(soc, "i", timeout_cycles=8 * BOOT_CYCLES)


def test_sd_info():
    soc = start(files={BIN: stage2(version=3), OLD: broken(stage2())})
    to_prompt(soc)
    out = cmd(soc, "i")
    assert "[SD] SDHC card\n" in out
    assert f"[FAT] FAT32 at LBA {FAT_LBA}, 512 B clusters\n" in out
    assert '[FAT] \\VUX9K\\STAGE2.BIN: v3 "VUX9K stage 2 (provisional)" ' in out
    assert " B [OK]\n" in out
    assert "[FAT] \\VUX9K\\STAGE2.OLD: CRC mismatch\n" in out
    soc = start(bytes(1024 * 1024))
    to_prompt(soc)
    assert "[FAT] no 55AA signature" in cmd(soc, "i")


def test_soft_reset_returns_to_the_first_stage():
    soc, _ = boot()
    # From the second stage: the first stage boots it again
    mark = len(soc.uart_received())
    out = cmd(soc, "r", until=S2_PROMPT)
    assert BANNER in out and "[BOOT] STAGE2.BIN " in out
    assert len(soc.uart_received()) > mark
    # From the first stage's prompt
    to_prompt_after = start(files={BIN: stage2()})
    to_prompt(to_prompt_after)
    out = cmd(to_prompt_after, "r", until=S2_PROMPT)
    assert "[RESET] Soft reset" in out and BANNER in out


def test_sdsc_card():
    """A strict SDSC card: byte addresses, and init at 400 kHz or less until ready"""
    soc, out = boot(sd_card={"sdhc": False, "strict": True})
    assert handoff(out) == (0, FAT_LBA, STAGE1_VERSION)
    assert not [v for v in soc.sd_violations if not v.startswith("power-up")], soc.sd_violations


def test_sd_v1_card():
    """A card without CMD8 (SD v1): ACMD41 without HCS, byte addresses"""
    soc, out = boot(sd_card={"mute_cmds": [8], "sdhc": False, "strict": True})
    assert handoff(out)[0] == 0
    assert not [v for v in soc.sd_violations if not v.startswith("power-up")], soc.sd_violations


def test_card_without_cmd55_initializes_with_cmd1():
    soc, _ = boot(sd_card={"mute_cmds": [55]})
    assert (1, 0x4000_0000) in soc.sd_commands


def test_card_that_never_becomes_ready():
    soc = start(card({BIN: stage2()}), sd_card={"never_ready": True})
    out = wait_for(soc, S1_PROMPT, 8 * BOOT_CYCLES)  # each init attempt spends ~63M cycles
    assert "[BOOT] No SD card\n" in out


def test_exception_is_reported():
    soc = start(files={BIN: stage2()})
    to_prompt(soc)
    soc.run(ms(1))
    pc = soc.pc  # the next instruction of the prompt's idle loop
    mark = len(soc.uart_received())
    soc.load_iram_words([0], start=pc // 4)  # all-zero word: illegal instruction
    out = wait_for(soc, b"Reset the board.\n", ms(20), start=mark)
    assert f"[TRAP] mcause=0x00000002 mepc=0x{pc:08X} mtval=0x00000000" in out, out
    end = len(soc.uart_received())
    soc.run(ms(300))
    assert soc.leds in (0x2A, 0x15)
    assert len(soc.uart_received()) == end


def test_finds_files_past_long_names_and_cluster_ends():
    """mkfs.fat/mtools' card: with STAGE2.OLD deleted, looking for it walks the whole of
    \\VUX9K\\ (long names, one across its first cluster's end)."""
    img = bytearray(fixture("mkfs_s1"))
    img[entry_at(img, b"STAGE2  OLD")] = 0xE5
    soc = start(bytes(img))
    to_prompt(soc)
    out = cmd(soc, "i")
    assert '[FAT] \\VUX9K\\STAGE2.BIN: v2 "Stage2" 1500 B [OK]' in out, out
    assert "[FAT] \\VUX9K\\STAGE2.OLD: not found" in out


def test_broken_volumes():
    def boot_error(img):
        soc = start(bytes(img))
        return wait_for(soc, S1_PROMPT, BOOT_CYCLES)

    base = card({BIN: stage2()})
    # The BPB's root cluster outside the volume
    img = bytearray(base)
    img[FAT_LBA * 512 + 44 : FAT_LBA * 512 + 48] = (0).to_bytes(4, "little")
    assert "[BOOT] bad FAT32 boot sector\n" in boot_error(img)
    # STAGE2.BIN's chain: its first cluster's FAT entry points outside the volume
    img = bytearray(base)
    e = entry_at(img, b"STAGE2  BIN")
    first = int.from_bytes(img[e + 26 : e + 28], "little") | int.from_bytes(img[e + 20 : e + 22], "little") << 16
    fat = FAT_LBA * 512 + int.from_bytes(img[FAT_LBA * 512 + 14 : FAT_LBA * 512 + 16], "little") * 512
    img[fat + 4 * first : fat + 4 * first + 4] = (1).to_bytes(4, "little")
    out = boot_error(img)
    assert f"STAGE2.BIN: bad cluster chain at cluster {first}; STAGE2.OLD: not found" in out, out
    # An empty STAGE2.BIN
    assert "STAGE2.BIN: not a VUX9 v4 file;" in boot_error(card({BIN: b""}))
    # A card error in the root directory, where \\VUX9K\\ is
    img = bytearray(base)
    root = entry_at(img, b"VUX9K      ")
    soc = start(bytes(img), sd_card={"bad_sectors": [root // 512]})
    out = wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert f"STAGE2.BIN: card error at LBA {root // 512}; STAGE2.OLD: card error at LBA {root // 512}" in out, out


def test_boot_again_after_a_failed_boot():
    """b after a boot that found no second stage: the card is already initialized"""
    soc = start(files={})
    wait_for(soc, S1_PROMPT, BOOT_CYCLES)
    assert "STAGE2.BIN: not found" in cmd(soc, "b")


def test_keys_from_before_a_soft_reset_are_not_a_key_press():
    """r while the help text is still going out: the bytes after it arrive before the
    reset and are dropped, so the first stage boots."""
    soc = start(files={BIN: stage2()})
    to_prompt(soc)
    soc.run(50 * UART_BIT)
    mark = len(soc.uart_received())
    soc.uart_send(b"hrAB")
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES, start=mark)
    assert "[RESET] Soft reset" in out and "[SAFE MODE]" not in out, out


def test_idle_prompt_blinks_led0():
    soc = start(files={BIN: stage2()})
    to_prompt(soc)
    seen = set()
    for _ in range(40):
        soc.run(ms(50))
        seen.add(soc.leds & 1)
    assert seen == {0, 1}
