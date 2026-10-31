# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
How the tests send the Zephyr irq_echo app (zephyr_workspace/irq_echo) a line it keeps up
with: CHUNK bytes back to back, then GAP_FRAMES UART frame times of silence, and so on.
The emulator (sim/emu/test_zephyr_irq_echo.py), the RTL (test_soc_zephyr_irq_echo.py) and
the board (scripts/hw_irq_echo.py) share these.

Running from the uncached PSRAM (before the instruction cache), its RX handler couldn't keep
up with 115200 baud back to back: a 64-byte line came back with 52 bytes once the 32-byte RX
FIFO had filled. Measured on the emulator then (its paced test prints it, the measurement to
tune GAP_FRAMES by), the FIFO was empty 69 frame times after a 16-byte chunk's first byte at
worst, about 4.3 frame times a byte. With 5 a byte, a chunk every CHUNK + GAP_FRAMES = 176
frame times was 2.5x the measurement; the emulator test fails below 2x. With the cache the
back-to-back line comes back whole; the paced line is what the board test sends by default.

The overrun test sends RX_FIFO + 1 bytes in the time hold_words holds the application back,
so it needs no race with the application's start: however fast it starts, the line has arrived
and overflowed the RX FIFO before the first instruction of it runs.
"""

from rv32_asm import Asm

CHUNK = 16  # bytes sent back to back: half the RX FIFO
HANDLER_FRAMES_PER_BYTE = 5  # the RX handler's frame times a byte: 4.3 uncached, ~1.1 with the caches
GAP_FRAMES = 2 * HANDLER_FRAMES_PER_BYTE * CHUNK  # silence after each chunk, in frame times
FRAME_BITS = 10  # start, 8 data, stop


def chunks(data: bytes, size: int = CHUNK) -> list[bytes]:
    """data in pieces of size bytes (the last one shorter)."""
    return [data[i : i + size] for i in range(0, len(data), size)]


RX_FIFO = 32  # soc's UART RX FIFO
OVERRUN_LINE = RX_FIFO + 1  # bytes: the shortest line that overflows it
SLOWEST_FRAME_CYCLES = 11 * 156  # a byte from VirtualSerialBridge, two stop bits
# How long hold_words waits: the line as the slowest sender sends it and 4 frames' margin
HOLD_CYCLES = (OVERRUN_LINE + 4) * SLOWEST_FRAME_CYCLES


def hold_words(target: int) -> list[int]:
    """ROM words that wait HOLD_CYCLES on mtime and then jump to target (4 KB-aligned), as
    soc_env.jump_words does at once: the UART is left alone, so its sticky bits and FIFO are
    what the line left them for the application to find."""
    assert target % 4096 == 0, hex(target)
    a = Asm()
    a.li("t0", 0x4000_1000)  # mtime
    a.li("t1", HOLD_CYCLES)
    a.lw("t2", 0, "t0")
    a.add("t1", "t1", "t2")
    a.label("wait")
    a.lw("t2", 0, "t0")
    a.bltu("t2", "t1", "wait")
    a.lui("t0", target >> 12)
    a.jalr("zero", "t0", 0)
    return a.assemble()
