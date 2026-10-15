# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
How the tests send the Zephyr irq_echo app (zephyr_workspace/irq_echo) a line it keeps up
with: CHUNK bytes back to back, then GAP_FRAMES UART frame times of silence, and so on.
The emulator (sim/emu/test_zephyr_irq_echo.py), the RTL (test_soc_zephyr_irq_echo.py) and
the board (scripts/hw_irq_echo.py) share these.

Running from the uncached PSRAM, its RX handler can't keep up with 115200 baud back to back:
a 64-byte line came back with 52 bytes once the 32-byte RX FIFO had filled (every third byte
lost), i.e. the handler takes about 3 frame times a byte. A chunk of 16 then takes ~48 frame
times to read; a chunk every CHUNK + GAP_FRAMES = 112 frame times leaves about twice that.
The 3 is an estimate from that pattern: the emulator test prints how long the FIFO takes to
drain after each chunk, the measurement to tune GAP_FRAMES by. The cache (B4a) should bring
the back-to-back line back.
"""

CHUNK = 16  # bytes sent back to back: half the RX FIFO
HANDLER_FRAMES_PER_BYTE = 3  # the RX handler's time per byte from the PSRAM, in frame times (estimate)
GAP_FRAMES = 2 * HANDLER_FRAMES_PER_BYTE * CHUNK  # silence after each chunk, in frame times
FRAME_BITS = 10  # start, 8 data, stop


def chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    """data in pieces of size bytes (the last one shorter)."""
    return [data[i : i + size] for i in range(0, len(data), size)]
