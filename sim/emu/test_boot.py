# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Power-on boot of the real firmware (firmware.hex + firmware_d0-3.hex) on the
emulator: Boot Manager prompt, and an S2-button launch of SD slot 1 through the
Resident Loader (the emulator counterpart of sim/integration/test_soc_fast.py).
"""

import struct

from vux9k import slot_image, start_soc, wait_for, press_button

# lui a1, 0x40000; addi a0, zero, '#'; sb a0, 0(a1); j .
HASH_PAYLOAD = struct.pack("<IIII", 0x400005B7, 0x02300513, 0x00A58023, 0x0000006F)


def test_boot_prompt_and_s2_launch():
    slot1, _ = slot_image(HASH_PAYLOAD, slot=1, mode="riscv", name="TestApp")
    soc = start_soc(sd_sectors=slot1)
    out = wait_for(soc, b"vux> ", timeout_cycles=3_000_000)
    assert "VUX9K" in out, out

    mark = len(soc.uart_received())
    press_button(soc)
    wait_for(soc, b"[RL] Slot 1", timeout_cycles=2_000_000, start=mark)
    wait_for(soc, b"#", timeout_cycles=2_000_000, start=mark)
    assert soc.riscv_mode
