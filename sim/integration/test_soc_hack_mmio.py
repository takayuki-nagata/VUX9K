# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SoC MMIO read decode in Hack 16-bit mode (test_soc_hack_mmio.py).

The Hack firmware (hack_demo/) only ever writes the UART, so the Hack-mode read
side of soc_top's address decoder (0x6000-0x600F: UART data/status, GPIO) is
otherwise unexercised. Small hand-assembled Hack programs (hack_asm) are
preloaded straight into I-RAM, read registers and print what they read as one
character each, which the tests compare against the expected values.

test_soc_hack_uart_rx guards a fixed bug: soc_top used to pop the UART RX FIFO
on every cycle the A register pointed at the UART (even for a status read), so a
received byte was gone before the program could see it. Now only a read of the
data register (mem_read_req) pops it.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
import hack_asm
from hack_asm import Asm
from soc_env import load_imem, start_soc

# Hack-mode MMIO window (soc_top: data_addr[15:4] == 0x600; [3:2] == 0 -> UART, else GPIO)
UART_DATA = 0x6000  # read: RX byte (pops the FIFO), write: TX byte
UART_STATUS = 0x6001  # {tx_full, rx_empty}
GPIO_BTN = 0x6004  # gpio_controller addr 4: button pressed (active-low pin, synchronized)
GPIO_SOFT_RST = 0x600C  # gpio_controller addr C: soft-reset pulse in progress
# Hack RAM is 0x0000-0x5FFF (d_mem indexed by addr[10:0]); these are neither RAM nor MMIO
RAM_PROBE = 0x0100
UNMAPPED_ALIAS = 0x6900  # addr[10:0] == RAM_PROBE: a write must not land in RAM
UNMAPPED_READ = 0x6010  # just past the MMIO window: reads as 0


def _print_reg(a: Asm, addr: int):
    """UART_DATA <- '0' + M[addr]"""
    a.at(addr)
    a.c("D=M")
    a.at(ord("0"))
    a.c("D=D+A")
    a.at(UART_DATA)
    a.c("M=D")


def _print_char(a: Asm, ch: str):
    a.at(ord(ch))
    a.c("D=A")
    a.at(UART_DATA)
    a.c("M=D")


def _store(a: Asm, addr: int, value: int):
    a.at(value)
    a.c("D=A")
    a.at(addr)
    a.c("M=D")


def status_gpio_program() -> list[int]:
    """Reads without any received byte: UART status, GPIO button and soft-reset status,
    then an unmapped write (must not alias into RAM) and an unmapped read (0)."""
    a = Asm()
    _print_reg(a, UART_STATUS)  # idle: rx_empty -> '1'
    _print_reg(a, GPIO_BTN)  # button held by the test: '1'
    _print_reg(a, GPIO_SOFT_RST)  # no soft reset: '0'
    _store(a, RAM_PROBE, ord("5"))
    _store(a, UNMAPPED_ALIAS, ord("Z"))
    a.at(RAM_PROBE)  # still '5'
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    _print_reg(a, UNMAPPED_READ)  # '0'
    _print_char(a, "\n")
    a.label("halt")
    a.at("halt")
    a.c("0;JMP")
    return a.assemble()


def uart_rx_program() -> list[int]:
    """Prompt, poll the status until a byte arrives, then report status / echo it / status."""
    a = Asm()
    _print_char(a, "?")  # prompt: the test sends one byte now
    a.label("wait_rx")
    a.at(UART_STATUS)
    a.c("D=M")
    a.at(1)
    a.c("D=D&A")
    a.at("wait_rx")
    a.c("D;JNE")
    _print_reg(a, UART_STATUS)  # byte waiting: '0'
    a.at(UART_DATA)  # echo the received byte
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    _print_reg(a, UART_STATUS)  # FIFO drained again: '1'
    _print_char(a, "\n")
    a.label("halt")
    a.at("halt")
    a.c("0;JMP")
    return a.assemble()


async def _start_hack(dut, program):
    ser, _ = await start_soc(dut, btn=0, during_reset=lambda soc: load_imem(soc, program))
    mode = int(dut.soc.active_mode.value)
    assert mode == 0, f"program must be detected as Hack (active_mode=0), got {mode}"
    return ser


@cocotb.test()
async def test_soc_hack_mmio_status_gpio(dut):
    """Hack mode reads UART status and GPIO through the address decoder; unmapped addresses are inert"""
    hack_asm.selftest()
    ser = await _start_hack(dut, status_gpio_program())
    out = (await ser.wait_for(b"\n", timeout_cycles=200_000)).decode("ascii", errors="replace")
    assert out == "11050\n", (
        f"expected '11050\\n' (UART status rx_empty, button, soft-reset, RAM after unmapped write, unmapped read), "
        f"got {out!r}"
    )
    dut._log.info("Hack-mode MMIO reads (UART status, GPIO) and unmapped accesses verified")


@cocotb.test()
async def test_soc_hack_uart_rx(dut):
    """Hack mode sees a received byte in the UART status and reads it from the data register"""
    ser = await _start_hack(dut, uart_rx_program())
    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.reset_input_buffer()
    ser.write(b"x")
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert out == "0x1\n", f"expected '0x1\\n' (status, echo, status), got {out!r}"
