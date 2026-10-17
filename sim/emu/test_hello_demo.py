# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
The hello demo (firmware/hello, \\VUX9K\\APPS\\HELLO.BIN of make apps) on the emulator: the
second stage's `run` starts it in place in the PSRAM, it prints a0-a2 (as the first stage
handed them to the second stage, which hands them on), ignores keys but `r`, and on `r`
soft-resets back to the first stage, which is still in the ROM and boots the second stage
again.
"""

import re

import pytest
from s1_env import (
    BANNER,
    BOOT_CYCLES,
    FAT_LBA,
    S2_BANNER,
    S2_PROMPT,
    STAGE1_DIR,
    STAGE1_VERSION,
    app,
    handoff,
    run_from_card,
)
from vux9k import UART_BIT, ms, read_hex_words, vux_tool, wait_for

LAST_LINE = b"[HELLO] Press r for the first stage.\n"
S1_WORDS = read_hex_words(f"{STAGE1_DIR}/firmware.hex")


def hello_regs(out: str):
    m = re.search(r"\[HELLO\] a0=0x([0-9A-F]{8}) a1=0x([0-9A-F]{8}) a2=0x([0-9A-F]{8})\n", out)
    assert m, out
    return tuple(int(g, 16) for g in m.groups())


def test_is_a_psram_application():
    data = app("HELLO.BIN")
    h = vux_tool.ImageHeader.parse(data)
    assert h is not None
    assert (h.mode, h.load_addr, h.entry_point) == (vux_tool.MODE_RISCV, 0x8000_0000, 0x8000_0000)
    assert h.size == len(data) - vux_tool.HEADER_LEN <= 4096


@pytest.mark.parametrize("sd_card,a0", [({}, 1), ({"sdhc": False, "strict": True}, 0)], ids=["sdhc", "sdsc"])
def test_prints_the_first_stages_handoff(sd_card, a0):
    soc, mark = run_from_card("HELLO.BIN", sd_card=sd_card)
    out = wait_for(soc, LAST_LINE, BOOT_CYCLES, start=mark)
    assert out.startswith("\nVUX9K hello from the PSRAM\n"), out
    s2_out = soc.uart_received()[:mark].decode()
    assert hello_regs(out) == handoff(s2_out) == (a0, FAT_LBA, STAGE1_VERSION)
    assert 0x8000_0000 <= soc.pc < 0x8000_1000
    # The first stage is still in the ROM
    assert all(soc.rom_word(4 * i) == w for i, w in enumerate(S1_WORDS))


def test_r_soft_resets_to_the_first_stage():
    soc, mark = run_from_card("HELLO.BIN")
    wait_for(soc, LAST_LINE, BOOT_CYCLES, start=mark)
    # Other keys are ignored
    end = len(soc.uart_received())
    soc.uart_send(b"x\r")
    soc.run(ms(5))
    assert len(soc.uart_received()) == end
    # `r` with the line's CR after it: the CR doesn't stop the first stage's autoboot
    soc.run(50 * UART_BIT)
    soc.uart_send(b"r\r")
    out = wait_for(soc, S2_PROMPT, BOOT_CYCLES, start=end)
    assert out.startswith("[HELLO] Soft reset\n"), out
    assert BANNER in out and S2_BANNER in out and "[SAFE MODE]" not in out, out
