# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SoC memory map / MMIO peripheral verification in RISC-V mode (test_soc_rv32i.py).

A small RV32I program (built with rv32_asm) is preloaded straight into I-RAM, so no
SD card or Boot Manager is involved. It checks the address decoder and peripherals
from the CPU's side and reports over the UART: "F<code>" on the first failed check,
"PASS" when all passed. See the check codes in mmio_program().
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
import rv32_asm
from rv32_asm import Asm
from soc_env import load_imem, start_soc

UART = 0x4000_0000  # +0 data, +4 status {frame_err, overrun, tx_full, rx_empty}
TIMER = 0x4000_1000  # +0/+4 mtime lo/hi, +8/+C mtimecmp lo/hi
GPIO = 0x4000_3000  # +0 LEDs, +4 button, +C soft-reset magic
DRAM = 0x2000_0000
RESET_MARKER_ADDR = DRAM + 0x200
RESET_MARKER = 0xC0FFEE42
GPIO_SOFT_RESET = 0x0000_A55A
LED_PATTERN = 0x2A


def _putc_routine(a: Asm):
    """putc(a0): wait while the TX FIFO is full, then write. Clobbers t5, t6."""
    a.label("putc")
    a.li("t5", UART)
    a.label("putc_wait")
    a.lw("t6", 4, "t5")
    a.andi("t6", "t6", 2)
    a.bnez("t6", "putc_wait")
    a.sb("a0", 0, "t5")
    a.ret()


def _fail_routine(a: Asm):
    """Print 'F' + check code letter (a1 = code, 'A' + code) and halt."""
    a.label("fail")
    a.mv("s1", "a1")
    a.li("a0", ord("F"))
    a.call("putc")
    a.addi("a0", "s1", ord("A"))
    a.call("putc")
    a.li("a0", ord("\n"))
    a.call("putc")
    a.label("halt_fail")
    a.j("halt_fail")


def _expect(a: Asm, reg: str, value: int, code: int):
    a.li("t2", value)
    a.li("a1", code)
    a.bne(reg, "t2", "fail")


def _puts(a: Asm, text: str):
    for ch in text:
        a.li("a0", ord(ch))
        a.call("putc")


def mmio_program() -> list[int]:
    a = Asm()
    # Second pass after a GPIO soft reset: the D-RAM marker survives (only the CPU resets)
    a.li("s0", RESET_MARKER_ADDR)
    a.lw("t0", 0, "s0")
    a.li("t1", RESET_MARKER)
    a.beq("t0", "t1", "after_soft_reset")

    # 1-3: D-RAM word/byte lanes and sub-word loads
    a.li("s2", DRAM + 0x100)
    a.li("t0", 0x11223344)
    a.sw("t0", 0, "s2")
    a.li("t0", 0xAA)
    a.sb("t0", 1, "s2")
    a.lw("t3", 0, "s2")
    _expect(a, "t3", 0x1122AA44, 1)
    a.lb("t3", 1, "s2")
    _expect(a, "t3", 0xFFFFFFAA, 2)
    a.lbu("t3", 2, "s2")
    _expect(a, "t3", 0x22, 3)

    # 4-5: I-RAM through the data port (read a constant, write + read back)
    a.la("s3", "table")
    a.lw("t3", 0, "s3")
    _expect(a, "t3", 0xDEADBEEF, 4)
    a.li("t0", 0x5A5A1234)
    a.sw("t0", 4, "s3")
    a.lw("t3", 4, "s3")
    _expect(a, "t3", 0x5A5A1234, 5)

    # 6-7: unmapped addresses read as 0 (unused MMIO page, unmapped region)
    a.li("t0", 0x4000_4000)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0, 6)
    a.li("t0", 0x8000_0000)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0, 7)

    # 8-9: mtime advances; mtimecmp reads back
    a.li("s4", TIMER)
    a.lw("t4", 0, "s4")
    for _ in range(4):
        a.nop()
    a.lw("t5", 0, "s4")
    a.li("a1", 8)
    a.bltu("t4", "t5", "timer_ok")
    a.j("fail")
    a.label("timer_ok")
    a.li("t0", 0x12345678)
    a.sw("t0", 8, "s4")
    a.lw("t3", 8, "s4")
    _expect(a, "t3", 0x12345678, 9)
    a.li("t0", 0xFFFFFFFF)
    a.sw("t0", 8, "s4")

    # 10-11: GPIO LED readback; button (held by the testbench) reads 1
    a.li("s5", GPIO)
    a.li("t0", LED_PATTERN)
    a.sw("t0", 0, "s5")
    a.lw("t3", 0, "s5")
    _expect(a, "t3", LED_PATTERN, 10)
    a.lw("t3", 4, "s5")
    _expect(a, "t3", 1, 11)

    # 12-13: UART RX: ask the testbench for a byte, read it, and the FIFO is empty again
    _puts(a, "?")
    a.li("t0", UART)
    a.label("rx_wait")
    a.lw("t1", 4, "t0")
    a.andi("t1", "t1", 1)
    a.bnez("t1", "rx_wait")
    a.lw("t3", 0, "t0")
    _expect(a, "t3", ord("x"), 12)
    a.lw("t1", 4, "t0")
    a.andi("t1", "t1", 1)
    _expect(a, "t1", 1, 13)

    # 15-17: aliasing, documented in README ("Address decoding and aliases"): D-RAM repeats
    # every 8 KB across 0x2xxx_xxxx, and data reads of 0x0000_0000-0x0000_FFFF are I-RAM
    # repeating every 16 KB
    a.li("t0", DRAM + 0x2100)
    a.li("t1", 0x0BADCAFE)
    a.sw("t1", 0, "t0")
    a.lw("t3", 0, "s2")  # s2 = DRAM + 0x100
    _expect(a, "t3", 0x0BADCAFE, 15)
    a.li("t0", 0x2400_0100)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0x0BADCAFE, 16)
    a.li("t0", 0x4000)
    a.add("t0", "t0", "s3")  # s3 = &table
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0xDEADBEEF, 17)

    # 14: GPIO soft reset restarts the CPU at 0 (continues at after_soft_reset)
    a.li("t1", RESET_MARKER)
    a.sw("t1", 0, "s0")
    _puts(a, "S")
    a.li("t0", GPIO_SOFT_RESET)
    a.sw("t0", 0xC, "s5")
    a.li("a1", 14)
    for _ in range(64):  # the reset pulse lands within a few cycles
        a.nop()
    a.j("fail")

    a.label("after_soft_reset")
    a.sw("zero", 0, "s0")
    _puts(a, "RPASS\n")
    a.label("halt_ok")
    a.j("halt_ok")

    _putc_routine(a)
    _fail_routine(a)
    a.label("table")
    a.word(0xDEADBEEF)
    a.word(0)
    return a.assemble()


def timer_irq_program() -> list[int]:
    """Enable the machine timer interrupt; the handler prints 'I' and checks mcause."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s4", TIMER)
    a.li("t0", 0xFFFFFFFF)
    a.sw("t0", 0xC, "s4")  # mtimecmp hi first, so no spurious match while updating
    a.lw("t1", 0, "s4")
    a.addi("t1", "t1", 200)
    a.sw("t1", 8, "s4")
    a.sw("zero", 0xC, "s4")
    a.li("t0", 0x80)  # mie.MTIE
    a.csrs("mie", "t0")
    a.li("t0", 0x8)  # mstatus.MIE
    a.csrs("mstatus", "t0")
    a.li("s1", 0)  # set to 1 by the handler
    a.li("t3", 3000)
    a.label("spin")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "spin")
    a.bnez("s1", "irq_taken")
    _puts(a, "IRQ-NONE\n")
    a.label("halt1")
    a.j("halt1")
    a.label("irq_taken")
    _puts(a, "IRQ-OK\n")
    a.label("halt2")
    a.j("halt2")

    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", 0x80000007)
    a.bne("t0", "t1", "bad_cause")
    a.li("t0", 0xFFFFFFFF)
    a.sw("t0", 0xC, "s4")  # push mtimecmp out of reach -> deassert the interrupt
    a.li("s1", 1)
    a.mret()
    a.label("bad_cause")
    _puts(a, "IRQ-BADCAUSE\n")
    a.label("halt3")
    a.j("halt3")

    _putc_routine(a)
    return a.assemble()


def uart_status_program() -> list[int]:
    """UART register decode and the sticky RX error flags, reported as 'F<code>' or 'UPASS'."""
    a = Asm()
    a.li("s0", UART)
    # Only the data register (offset 0) transmits: this must not show up on TX
    a.li("t0", ord("Z"))
    a.sw("t0", 4, "s0")
    # Ask for 33 bytes and don't read them: the 33rd overruns the 32-deep RX FIFO
    _puts(a, "?")
    a.li("t3", 60000)  # 33 frames take ~80k cycles after the '?'; this waits ~240k
    a.label("spin")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "spin")
    # 1: the first status read shows the overrun (bit 2) and a non-empty FIFO
    a.lw("t1", 4, "s0")
    a.andi("t1", "t1", 0xD)
    _expect(a, "t1", 0x4, 1)
    # 2: reading the status cleared it
    a.lw("t1", 4, "s0")
    a.andi("t1", "t1", 0x4)
    _expect(a, "t1", 0, 2)
    _puts(a, "UPASS\n")
    a.label("halt")
    a.j("halt")

    _putc_routine(a)
    _fail_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_uart_status(dut):
    """TX only from the data register; RX overrun flagged in status bit 2, cleared by reading it"""
    program = uart_status_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    before = await ser.wait_for(b"?", timeout_cycles=200_000)
    assert before == b"?", f"a write to the status register was transmitted: {before!r}"
    ser.write(bytes(range(0x40, 0x61)))  # 33 bytes
    out = (await ser.wait_for(b"\n", timeout_cycles=1_000_000)).decode("ascii", errors="replace")
    assert out.strip() == "UPASS", f"UART status check failed: {out!r} (see uart_status_program() codes)"


def uart_rx_irq_program() -> list[int]:
    """Enable the external interrupt (UART RX); the handler checks mcause and reads the byte."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s0", UART)
    a.li("s1", 0)  # set to the received byte by the handler
    a.li("t0", 0x800)  # mie.MEIE
    a.csrs("mie", "t0")
    a.li("t0", 0x8)  # mstatus.MIE
    a.csrs("mstatus", "t0")
    _puts(a, "?")
    a.li("t3", 20000)
    a.label("spin")
    a.bnez("s1", "irq_taken")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "spin")
    _puts(a, "EIRQ-NONE\n")
    a.label("halt1")
    a.j("halt1")
    a.label("irq_taken")
    a.li("t1", ord("k"))
    a.bne("s1", "t1", "bad_byte")
    _puts(a, "EIRQ-OK\n")
    a.label("halt2")
    a.j("halt2")
    a.label("bad_byte")
    _puts(a, "EIRQ-BADBYTE\n")
    a.label("halt4")
    a.j("halt4")

    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", 0x8000000B)
    a.bne("t0", "t1", "bad_cause")
    a.lw("s1", 0, "s0")  # reading the byte empties the RX FIFO -> the interrupt deasserts
    a.mret()
    a.label("bad_cause")
    _puts(a, "EIRQ-BADCAUSE\n")
    a.label("halt3")
    a.j("halt3")

    _putc_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_uart_rx_interrupt(dut):
    """A received byte raises the machine external interrupt (mcause 0x8000000B) when MEIE is set"""
    program = uart_rx_irq_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.write(b"k")
    out = (await ser.wait_for(b"\n", timeout_cycles=1_000_000)).decode("ascii", errors="replace")
    assert out.strip() == "EIRQ-OK", f"UART RX interrupt not taken/handled: {out!r}"


@cocotb.test()
async def test_soc_mmio_map(dut):
    """Address decoder, D-RAM lanes, I-RAM data port, timer, GPIO, UART RX and soft reset"""
    rv32_asm.selftest()
    program = mmio_program()
    ser, _ = await start_soc(dut, btn=0, during_reset=lambda soc: load_imem(soc, program))
    assert int(dut.soc.active_mode.value) == 1, "program must be detected as RISC-V"

    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.write(b"x")
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert "F" not in out, f"MMIO check failed with code {out!r} (see mmio_program() check numbers)"
    assert "SRPASS" in out, f"expected soft-reset marker and PASS, got {out!r}"

    led = int(dut.led.value)
    assert led == (~LED_PATTERN & 0x3F), f"LED pins 0b{led:06b}, expected active-low 0x{LED_PATTERN:02X}"
    dut._log.info("SoC memory map and MMIO peripherals verified")


@cocotb.test()
async def test_soc_timer_interrupt(dut):
    """Timer interrupt traps to mtvec with mcause 0x80000007, and mret returns"""
    program = timer_irq_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert out.strip() == "IRQ-OK", f"timer interrupt not taken/handled: {out!r}"
