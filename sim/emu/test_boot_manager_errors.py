# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Boot Manager on the emulator: the paths a healthy card and a cooperative host don't
take. No card or unreadable sectors, SD v1 and CMD1-only cards, the catalog's
Hack/unnamed/system slots, and every error of the 'w' protocol.
"""

from bm_env import BOOT_CYCLES, HASH_PAYLOAD, PROMPT, boot, cmd
from vux9k import UART_BIT, ms, slot_image, slot_lba, start_soc, vux_tool, wait_for


def send(soc, data: bytes):
    soc.run(50 * UART_BIT)
    soc.uart_send(data)


def write_until(soc, until: bytes, *chunks: bytes, timeout=None) -> str:
    """'w', then each chunk after the Boot Manager's next [READY...] line."""
    timeout = timeout or ms(6000)  # longer than the protocol's 5 s timeouts
    start = len(soc.uart_received())
    send(soc, b"w")
    for c in chunks:
        mark = len(soc.uart_received())
        wait_for(soc, b"]\n", timeout, start=mark)
        send(soc, c)
    return wait_for(soc, until, timeout, start=start)


def test_diag_sees_the_button_pressed():
    soc, _ = boot()
    start = len(soc.uart_received())
    send(soc, b"t")
    wait_for(soc, b"[CMD:0x74]", ms(10), start=start)  # press only once 't' is being run
    soc.set_button(True)
    wait_for(soc, b"PRESSED [PASS]", ms(3000))
    soc.set_button(False)
    wait_for(soc, PROMPT, ms(3000), start=len(soc.uart_received()))


def test_commands_without_a_card():
    soc, _ = boot(card=False)
    assert "[SD] Card init failed!" in cmd(soc, "d", timeout_cycles=BOOT_CYCLES)
    assert "[SD] Init Failed / No Card!" in cmd(soc, "i", timeout_cycles=BOOT_CYCLES)
    out = write_until(soc, b"Failed to initialize SD card!", b"\x03", b"\x01", timeout=BOOT_CYCLES)
    assert "[READY-COUNT:1]" in out


def test_unreadable_sector_0():
    soc, _ = boot(sd_card={"bad_sectors": [0]})
    assert "[SD] Read sector 0 failed!" in cmd(soc, "d", timeout_cycles=BOOT_CYCLES)


def test_catalog_of_system_hack_and_unnamed_slots():
    raw0, _ = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=0, name="Sys", mode="riscv", version=1)
    hack, _ = slot_image(bytes(8), slot=2, mode="hack", name="HackOne")
    raw3, _ = vux_tool.build_vux9_image(HASH_PAYLOAD, slot=3, name="", mode="riscv")
    raw3 = bytearray(raw3)
    raw3[32:64] = bytes(32)  # no name at all
    soc, _ = boot(sd_sectors={slot_lba(0): raw0[:512], **hack, slot_lba(3): bytes(raw3[:512])})
    out = cmd(soc, "l", timeout_cycles=BOOT_CYCLES)
    assert '"Sys" [RV32I, 16 B] (Boot Manager)' in out, out
    assert '"HackOne" [Hack 16b, ' in out, out
    assert '"(unnamed)" [RV32I' in out, out
    assert "  Mode:  0 (Hack 16-bit ISA)" in cmd(soc, "s2")


def test_enter_redraws_the_prompt():
    soc, _ = boot()
    start = len(soc.uart_received())
    send(soc, b"\r")
    assert wait_for(soc, PROMPT, ms(100), start=start) == "\nvux> "


def test_write_drains_stale_input_and_rejects_a_bad_slot():
    soc, _ = boot()
    start = len(soc.uart_received())
    # 'k' keeps the Boot Manager busy while "wxyz" queues up in the RX FIFO: 'w' then
    # finds "xyz" waiting and discards it before [READY]
    send(soc, b"kwxyz")
    wait_for(soc, b"[READY]\n", ms(3000), start=start)
    mark = len(soc.uart_received())
    send(soc, b"\x0b")  # not \n: line ends are skipped
    assert "[SD-ERR] Invalid slot ID: 0x0B" in wait_for(soc, PROMPT, ms(6000), start=mark)


def test_write_rejects_a_bad_sector_count():
    soc, _ = boot()
    assert "[SD-ERR] Invalid sector count: 0x41" in write_until(soc, PROMPT, b"\x01", b"\x41")


def test_write_times_out_waiting_for_the_count_or_the_data():
    soc, _ = boot()
    assert "[SD-ERR] Sector count timeout!" in write_until(soc, PROMPT, b"\x01")
    out = write_until(soc, PROMPT, b"\r\x01", b"\n\x01", b"x" * 100)
    assert "[SD-ERR] Timeout at sector 0, byte 100" in out, out


def test_write_reports_a_rejected_block():
    soc, _ = boot(sd_card={"write_reject": True})
    out = write_until(soc, PROMPT, b"\x05", b"\x01", bytes(512), timeout=BOOT_CYCLES)
    assert f"[SD-ERR] Failed to write block at sector {slot_lba(5)}" in out, out


def test_sd_v1_card():
    """A card without CMD8 (SD v1): initialized with ACMD41 without HCS, byte addressing"""
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"mute_cmds": [8], "sdhc": False, "strict": True})
    assert "Card Ready! [OK]" in cmd(soc, "i", timeout_cycles=BOOT_CYCLES)
    assert "CRC32: 0x" in cmd(soc, "s1", timeout_cycles=BOOT_CYCLES)
    assert not [v for v in soc.sd_violations if not v.startswith("power-up")], soc.sd_violations


def test_card_without_cmd55_initializes_with_cmd1():
    """CMD55 unanswered (an MMC-style card): the Boot Manager initializes with CMD1"""
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv")
    soc, _ = boot(sd_sectors=slot1, sd_card={"mute_cmds": [55]})
    assert "Card Ready! [OK]" in cmd(soc, "i", timeout_cycles=BOOT_CYCLES)
    assert (1, 0x4000_0000) in soc.sd_commands


def test_card_that_never_becomes_ready():
    """ACMD41 never reports ready: the Boot Manager gives up after 1000 tries"""
    soc = start_soc(sd_card={"never_ready": True})
    wait_for(soc, PROMPT, 8 * BOOT_CYCLES)  # each init attempt spends ~63M cycles
    assert "[SD] Init Failed / No Card!" in cmd(soc, "i", timeout_cycles=4 * BOOT_CYCLES)
