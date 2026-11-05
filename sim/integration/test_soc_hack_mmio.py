# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SoC MMIO read decode in Hack 16-bit mode (test_soc_hack_mmio.py).

The Hack firmware (hack_demo/) only ever writes the UART, so the Hack-mode read
side of soc_top's address decoder (0x6000-0x600F: the UART's first four registers, GPIO) is
otherwise unexercised. Small hand-assembled Hack programs (hack_asm), started in
Hack's I segment by soc_env.start_hack, read registers and print what they read
as one character each, which the tests compare against the expected values.

test_soc_hack_uart_rx guards a fixed bug: soc_top used to pop the UART RX FIFO
on every cycle the A register pointed at the UART (even for a status read), so a
received byte was gone before the program could see it. Now only a read of RXTX
(mem_read_req) pops it.
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
import hack_asm
from hack_asm import Asm
from soc_env import start_hack

# Hack-mode MMIO window (soc_addr_decoder: addr[15:4] == 0x600; [3:2] == 0 -> UART, else
# GPIO); the UART's word k is its register at offset 4k (uart_regs)
UART_DATA = 0x6000  # RXTX. read: RX byte (pops the FIFO), write: TX byte
UART_TXFULL = 0x6001
UART_RXEMPTY = 0x6002
UART_EV_STATUS = 0x6003  # bit 0 TX (not full), bit 1 RX (not empty)
GPIO_BTN = 0x6004  # gpio_controller addr 4: button pressed (active-low pin, synchronized)
GPIO_SOFT_RST = 0x600C  # gpio_controller addr C: soft-reset pulse in progress
# Hack RAM is 0x0000-0x5FFF, one PSRAM word each in Hack's D segment (no alias)
RAM_PROBE = 0x0100
UNMAPPED_ALIAS = 0x6900  # addr[10:0] == RAM_PROBE: a write must not land in RAM
UNMAPPED_READ = 0x6010  # just past the MMIO window: reads as 0
RAM_OLD_ALIAS = 0x0900  # the same word as RAM_PROBE when Hack RAM was D-RAM's 2K words


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
    """Reads without any received byte: UART RXEMPTY, TXFULL and EV_STATUS (stores to them
    ignored), GPIO button and soft-reset status,
    then an unmapped write (must not alias into RAM), an unmapped read (0) and a
    write to RAM_OLD_ALIAS, which is a word of its own (RAM_PROBE keeps its value)."""
    a = Asm()
    for addr in (UART_TXFULL, UART_RXEMPTY, UART_EV_STATUS):
        _store(a, addr, 3)  # read-only: ignored
    _print_reg(a, UART_RXEMPTY)  # idle: '1'
    _print_reg(a, UART_TXFULL)  # '0'
    _print_reg(a, UART_EV_STATUS)  # TX only: '1'
    _print_reg(a, GPIO_BTN)  # button held by the test: '1'
    _print_reg(a, GPIO_SOFT_RST)  # no soft reset: '0'
    _store(a, RAM_PROBE, ord("5"))
    _store(a, UNMAPPED_ALIAS, ord("Z"))
    a.at(RAM_PROBE)  # still '5'
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    _print_reg(a, UNMAPPED_READ)  # '0'
    _store(a, RAM_OLD_ALIAS, ord("7"))
    a.at(RAM_PROBE)  # still '5'
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    a.at(RAM_OLD_ALIAS)  # '7'
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    _print_char(a, "\n")
    a.label("halt")
    a.at("halt")
    a.c("0;JMP")
    return a.assemble()


def uart_rx_program() -> list[int]:
    """Prompt, poll RXEMPTY until a byte arrives, then report RXEMPTY and EV_STATUS / echo it /
    RXEMPTY and EV_STATUS."""
    a = Asm()
    _print_char(a, "?")  # prompt: the test sends one byte now
    a.label("wait_rx")
    a.at(UART_RXEMPTY)
    a.c("D=M")
    a.at("wait_rx")
    a.c("D;JNE")
    _print_reg(a, UART_RXEMPTY)  # byte waiting: '0'
    _print_reg(a, UART_EV_STATUS)  # both events: '3'
    a.at(UART_DATA)  # echo the received byte
    a.c("D=M")
    a.at(UART_DATA)
    a.c("M=D")
    _print_reg(a, UART_RXEMPTY)  # FIFO drained again: '1'
    _print_reg(a, UART_EV_STATUS)  # TX only: '1'
    _print_char(a, "\n")
    a.label("halt")
    a.at("halt")
    a.c("0;JMP")
    return a.assemble()


async def _start_hack(dut, program):
    ser, _ = await start_hack(dut, program, btn=0)
    return ser


@cocotb.test()
async def test_soc_hack_mmio_status_gpio(dut):
    """Hack mode reads the UART's registers and GPIO through the address decoder; unmapped addresses are inert"""
    hack_asm.selftest()
    ser = await _start_hack(dut, status_gpio_program())
    out = (await ser.wait_for(b"\n", timeout_cycles=200_000)).decode("ascii", errors="replace")
    assert out == "101105057\n", (
        "expected '101105057\\n' (UART RXEMPTY, TXFULL, EV_STATUS, button, soft-reset, RAM after "
        f"unmapped write, unmapped read, RAM after a write to its old alias, the old alias), got {out!r}"
    )
    dut._log.info("Hack-mode MMIO reads (UART registers, GPIO) and unmapped accesses verified")


@cocotb.test()
async def test_soc_hack_uart_rx(dut):
    """Hack mode sees a received byte in RXEMPTY and EV_STATUS and reads it from RXTX"""
    ser = await _start_hack(dut, uart_rx_program())
    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.reset_input_buffer()
    ser.write(b"x")
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert out == "03x11\n", f"expected '03x11\\n' (RXEMPTY, EV_STATUS, echo, RXEMPTY, EV_STATUS), got {out!r}"
