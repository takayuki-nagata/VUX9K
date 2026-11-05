# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SoC memory map / MMIO peripheral verification in RISC-V mode (test_soc_rv32i.py).

A small RV32I program (built with rv32_asm) is preloaded straight into the boot ROM, so no
SD card or boot firmware is involved. It checks the address decoder and peripherals
from the CPU's side and reports over the UART: "F<code>" on the first failed check,
"PASS" when all passed. See the check codes in mmio_program().
"""

import os
import sys

import cocotb

sys.path.append(os.path.dirname(__file__))
import rv32_asm
from rv32_asm import Asm
from soc_env import check_psram_pins, load_imem, start_soc

UART = 0x4000_0000  # liteuart's registers (uart_regs), each a word:
UART_TXFULL, UART_RXEMPTY, UART_EV_STATUS, UART_EV_PENDING = 0x04, 0x08, 0x0C, 0x10  # offsets
UART_EV_ENABLE, UART_TXEMPTY, UART_RXFULL = 0x14, 0x18, 0x1C
PAGE1 = 0x4000_1000  # MMIO page 1, the TIMER page until the CLINT replaced it: unassigned
GPIO = 0x4000_3000  # +0 LEDs, +4 button, +C soft-reset magic
CLINT = 0x2000_0000  # region 2: +0 msip, +0x4000/+0x4004 mtimecmp lo/hi, +0xBFF8/+0xBFFC mtime lo/hi
CLINT_MTIMECMP = CLINT + 0x4000  # lo, hi at +4
CLINT_MTIME = CLINT + 0xBFF8  # lo, hi at +4
UNASSIGNED = 0x3000_0000  # region 3: reads 0 and ignores stores
XMEM = 0x8000_0000  # wait-state window: the 8 MB PSRAM, repeating every 8 MB across region 8
XMEM_BYTES = 0x80_0000
GPIO_SOFT_RESET = 0x0000_A55A
LED_CHECK = 0x15  # check 10's pattern
LED_PATTERN = 0x2A  # set just before the soft reset: the second pass's marker


def _putc_routine(a: Asm):
    """putc(a0): wait while the TX FIFO is full, then write. Clobbers t5, t6."""
    a.label("putc")
    a.li("t5", UART)
    a.label("putc_wait")
    a.lw("t6", UART_TXFULL, "t5")
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
    # Second pass after a GPIO soft reset: the LED register, reset only at power-on, still
    # holds LED_PATTERN (only the CPU resets; the PSRAM has no defined power-up contents to
    # keep a marker in)
    a.li("s5", GPIO)
    a.lw("t0", 0, "s5")
    a.li("t1", LED_PATTERN)
    a.beq("t0", "t1", "after_soft_reset")

    # 1-3: region 3 is unassigned: a word and a byte stored, then every width reads 0
    a.li("s2", UNASSIGNED + 0x100)
    a.li("t0", 0x11223344)
    a.sw("t0", 0, "s2")
    a.li("t0", 0xAA)
    a.sb("t0", 1, "s2")
    a.lw("t3", 0, "s2")
    _expect(a, "t3", 0, 1)
    a.lb("t3", 1, "s2")
    _expect(a, "t3", 0, 2)
    a.lbu("t3", 2, "s2")
    _expect(a, "t3", 0, 3)

    # 4-5: the boot ROM through the data port: a constant reads back, a store to it is
    # ignored
    a.la("s3", "table")
    a.lw("t3", 0, "s3")
    _expect(a, "t3", 0xDEADBEEF, 4)
    a.li("t0", 0x5A5A1234)
    a.sw("t0", 4, "s3")
    a.lw("t3", 4, "s3")
    _expect(a, "t3", 0x600DC0DE, 5)

    # 6-7: unmapped addresses read as 0 (unused MMIO page, unmapped region)
    a.li("t0", 0x4000_4000)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0, 6)
    a.li("t0", 0x9000_0000)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0, 7)

    # 18-22: the wait-state window (the PSRAM; the controller's power-up isn't accelerated
    # on Icarus, so the first access may wait for it): a word and byte stores on each die's
    # lanes (bytes 0/2 die 0, 1/3 die 1), read back through the alias 8 MB up and from the
    # top of region 8, 0x8FFF_F800 being 0x807F_F800 (18-19); 0x8080_0020, CR0 if the
    # controller's register space were reachable, is the word at 0x8000_0020 (21); the
    # last word (22); code copied there runs (fence.i first) (20)
    a.li("s6", XMEM)
    a.li("t0", 0xCAFE_BABE)
    a.sw("t0", 0x20, "s6")
    a.li("t0", 0x11)
    a.sb("t0", 0x22, "s6")
    a.li("t0", XMEM + XMEM_BYTES)
    a.lw("t3", 0x20, "t0")
    _expect(a, "t3", 0xCA11_BABE, 18)
    a.li("s7", XMEM + XMEM_BYTES - 0x800)
    a.li("t0", 0x1234_5678)
    a.sw("t0", 0x24, "s7")
    a.li("t0", 0x8FFF_F800)
    a.li("t1", 0x99)
    a.sb("t1", 0x25, "t0")
    a.lw("t3", 0x24, "s7")
    _expect(a, "t3", 0x1234_9978, 19)
    a.li("t0", 0x0BAD_C0DE)
    a.li("t1", XMEM + XMEM_BYTES + 0x20)
    a.sw("t0", 0, "t1")
    a.lw("t3", 0x20, "s6")
    _expect(a, "t3", 0x0BAD_C0DE, 21)
    a.li("t0", 0x600D_F00D)
    a.li("t1", XMEM + XMEM_BYTES - 4)
    a.sw("t0", 0, "t1")
    a.li("t1", 0x8FFF_FFFC)
    a.lw("t3", 0, "t1")
    _expect(a, "t3", 0x600D_F00D, 22)
    a.li("t0", rv32_asm.i_type(0x5A, "zero", 0, "a2", 0x13))  # addi a2, zero, 0x5A
    a.sw("t0", 0x100, "s6")
    a.li("t0", rv32_asm.i_type(0, "ra", 0, "zero", 0x67))  # ret
    a.sw("t0", 0x104, "s6")
    a.word(0x0000_100F)  # fence.i
    a.addi("t0", "s6", 0x100)
    a.jalr("ra", "t0", 0)
    _expect(a, "a2", 0x5A, 20)

    # 8-9: mtime advances; mtimecmp reads back
    a.li("s4", CLINT_MTIME)
    a.lw("t4", 0, "s4")
    for _ in range(4):
        a.nop()
    a.lw("t5", 0, "s4")
    a.li("a1", 8)
    a.bltu("t4", "t5", "timer_ok")
    a.j("fail")
    a.label("timer_ok")
    a.li("s4", CLINT_MTIMECMP)
    a.li("t0", 0x12345678)
    a.sw("t0", 0, "s4")
    a.lw("t3", 0, "s4")
    _expect(a, "t3", 0x12345678, 9)
    a.li("t0", 0xFFFFFFFF)
    a.sw("t0", 0, "s4")

    # 10-11: GPIO LED readback; button (held by the testbench) reads 1
    a.li("t0", LED_CHECK)
    a.sw("t0", 0, "s5")
    a.lw("t3", 0, "s5")
    _expect(a, "t3", LED_CHECK, 10)
    a.lw("t3", 4, "s5")
    _expect(a, "t3", 1, 11)

    # 12-13: UART RX: ask the testbench for a byte, read it, and the FIFO is empty again
    _puts(a, "?")
    a.li("t0", UART)
    a.label("rx_wait")
    a.lw("t1", UART_RXEMPTY, "t0")
    a.bnez("t1", "rx_wait")
    a.lw("t3", 0, "t0")
    _expect(a, "t3", ord("x"), 12)
    a.lw("t1", UART_RXEMPTY, "t0")
    _expect(a, "t1", 1, 13)

    # 15-17: aliasing, documented in README ("Address decoding and aliases"): data reads
    # of region 0 are the ROM repeating every 16 KB, up to its top (until D-RAM went, the
    # region above 64 KB read D-RAM); an unassigned region reads 0 all over
    a.li("t0", 0x0FFF_C000)
    a.add("t0", "t0", "s3")  # s3 = &table
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0xDEADBEEF, 15)
    a.li("t0", UNASSIGNED + 0x0400_0100)
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0, 16)
    a.li("t0", 0x4000)
    a.add("t0", "t0", "s3")
    a.lw("t3", 0, "t0")
    _expect(a, "t3", 0xDEADBEEF, 17)

    # 14: GPIO soft reset restarts the CPU at 0 (continues at after_soft_reset)
    a.li("t1", LED_PATTERN)
    a.sw("t1", 0, "s5")
    _puts(a, "S")
    a.li("t0", GPIO_SOFT_RESET)
    a.sw("t0", 0xC, "s5")
    a.li("a1", 14)
    for _ in range(64):  # the reset pulse lands within a few cycles
        a.nop()
    a.j("fail")

    a.label("after_soft_reset")
    _puts(a, "RPASS\n")
    a.label("halt_ok")
    a.j("halt_ok")

    _putc_routine(a)
    _fail_routine(a)
    a.label("table")
    a.word(0xDEADBEEF)
    a.word(0x600DC0DE)
    return a.assemble()


def timer_irq_program() -> list[int]:
    """Enable the machine timer interrupt; the handler prints 'I' and checks mcause."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s4", CLINT_MTIMECMP)
    a.li("t0", 0xFFFFFFFF)
    a.sw("t0", 4, "s4")  # mtimecmp hi first, so no spurious match while updating
    a.li("t1", CLINT_MTIME)
    a.lw("t1", 0, "t1")
    a.addi("t1", "t1", 200)
    a.sw("t1", 0, "s4")
    a.sw("zero", 4, "s4")
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
    a.sw("t0", 4, "s4")  # push mtimecmp out of reach -> deassert the interrupt
    a.li("s1", 1)
    a.mret()
    a.label("bad_cause")
    _puts(a, "IRQ-BADCAUSE\n")
    a.label("halt3")
    a.j("halt3")

    _putc_routine(a)
    return a.assemble()


def clint_program() -> list[int]:
    """The CLINT at region 2: mtimecmp reads back, MMIO page 1 (the old TIMER page) reads 0
    and ignores stores, and msip raises the machine software interrupt (mcause 0x80000003);
    'F<code>' or 'CLPASS'."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s2", CLINT)
    a.li("s3", CLINT + 0x4000)  # mtimecmp
    a.li("s4", CLINT + 0xBFF8)  # mtime
    a.li("s5", PAGE1)
    # 1: mtime counts: two reads through the CLINT, a few cycles apart
    a.lw("t0", 0, "s4")
    a.lw("t1", 0, "s4")
    a.sub("t3", "t1", "t0")
    a.li("a1", 1)
    a.li("t2", 16)
    a.bgeu("t3", "t2", "fail")
    a.beqz("t3", "fail")
    # 2: page 1 reads 0 where mtime was
    a.lw("t3", 0, "s5")
    _expect(a, "t3", 0, 2)
    # 8: mtimecmp written through the CLINT reads back; a store to page 1 where mtimecmp
    # was is ignored, and page 1 reads 0 there
    a.li("t0", 0x2468_ACE0)
    a.sw("t0", 0, "s3")
    a.li("t1", 0x1357_9BDF)
    a.sw("t1", 8, "s5")
    a.lw("t3", 0, "s3")
    _expect(a, "t3", 0x2468_ACE0, 8)
    a.lw("t3", 8, "s5")
    _expect(a, "t3", 0, 8)
    a.li("t0", -1)
    a.sw("t0", 0, "s3")
    # 3: msip reads back bit 0 only
    a.sw("t0", 0, "s2")
    a.lw("t3", 0, "s2")
    _expect(a, "t3", 1, 3)
    # 4: ... and is mip.MSIP
    a.csrr("t3", "mip")
    a.andi("t3", "t3", 8)
    _expect(a, "t3", 8, 4)
    # 5: with mie.MSIE and mstatus.MIE it traps; the handler clears msip
    a.li("s1", 0)
    a.li("t0", 8)
    a.csrs("mie", "t0")
    a.csrs("mstatus", "t0")
    a.nop()
    _expect(a, "s1", 1, 5)
    # 6: msip is clear again
    a.lw("t3", 0, "s2")
    _expect(a, "t3", 0, 6)
    _puts(a, "CLPASS\n")
    a.label("halt")
    a.j("halt")

    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", 0x80000003)
    a.li("a1", 7)
    a.bne("t0", "t1", "fail")
    a.sw("zero", 0, "s2")  # msip = 0: the interrupt deasserts
    a.addi("s1", "s1", 1)
    a.mret()

    _putc_routine(a)
    _fail_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_clint(dut):
    """The CLINT at 0x2000_0000: mtime, mtimecmp, msip raising MSI; MMIO page 1 unassigned"""
    program = clint_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    out = (await ser.wait_for(b"\n", timeout_cycles=200_000)).decode("ascii", errors="replace")
    assert out.strip() == "CLPASS", f"CLINT check failed: {out!r} (see clint_program() codes)"


def uart_regs_program() -> list[int]:
    """The UART's registers (liteuart's layout) and a byte dropped on a full RX FIFO, reported
    as 'F<code>' or 'UPASS'."""
    a = Asm()
    a.li("s0", UART)
    # Only RXTX transmits: stores to the other registers and to 0x20 must not show up on TX
    a.li("t0", ord("Z"))
    for off in (UART_TXFULL, UART_RXEMPTY, UART_EV_STATUS, UART_EV_PENDING, UART_TXEMPTY, UART_RXFULL, 0x20, 0xFC):
        a.sw("t0", off, "s0")
    # 1-7: idle: RX empty and not full, the TX event only, also in EV_PENDING (EV_STATUS while
    # EV_ENABLE is 0, as Linux's polled driver reads it), 0x20 reads 0, and RXEMPTY again
    # through the 256-byte repeat
    for code, (off, want) in enumerate(
        (
            (UART_RXEMPTY, 1),
            (UART_RXFULL, 0),
            (UART_EV_STATUS, 1),
            (UART_EV_PENDING, 1),
            (UART_EV_ENABLE, 0),
            (0x20, 0),
            (0x100 + UART_RXEMPTY, 1),
        ),
        start=1,
    ):
        a.lw("t1", off, "s0")
        _expect(a, "t1", want, code)
    # Ask for 33 bytes and don't read them: the 33rd finds the 32-deep RX FIFO full
    _puts(a, "?")
    a.li("t3", 60000)  # 33 frames take ~80k cycles after the '?'; this waits ~240k
    a.label("spin")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "spin")
    # 8-12: RX full and not empty, both events (also in EV_PENDING: EV_ENABLE 0), read twice:
    # no read but RXTX's has a side effect
    for code, (off, want) in enumerate(
        ((UART_RXFULL, 1), (UART_RXEMPTY, 0), (UART_EV_STATUS, 3), (UART_EV_PENDING, 3), (UART_RXFULL, 1)),
        start=8,
    ):
        a.lw("t1", off, "s0")
        _expect(a, "t1", want, code)
    # 13: the FIFO holds the first 32 bytes in order (0x40..0x5F), the 33rd was dropped
    a.li("t4", 0x40)
    a.li("t5", 0x60)
    a.label("drain")
    a.lw("t1", UART_RXEMPTY, "s0")
    a.li("a1", 13)
    a.bnez("t1", "fail")  # empty before 0x60
    a.lbu("t1", 0, "s0")  # a byte load pops it too (Zephyr's driver reads RXTX so)
    a.bne("t1", "t4", "fail")
    a.addi("t4", "t4", 1)
    a.bne("t4", "t5", "drain")
    # 14-16: empty now (the 33rd byte isn't there), not full, no RX event
    a.lw("t1", UART_RXEMPTY, "s0")
    _expect(a, "t1", 1, 14)
    a.lw("t1", UART_RXFULL, "s0")
    _expect(a, "t1", 0, 15)
    a.lw("t1", UART_EV_STATUS, "s0")
    a.andi("t1", "t1", 2)
    _expect(a, "t1", 0, 16)
    # 17-18: EV_ENABLE holds bits 1:0 (MEIE is off: nothing is taken)
    a.li("t0", -1)
    a.sw("t0", UART_EV_ENABLE, "s0")
    a.lw("t1", UART_EV_ENABLE, "s0")
    _expect(a, "t1", 3, 17)
    a.sw("zero", UART_EV_ENABLE, "s0")
    a.lw("t1", UART_EV_ENABLE, "s0")
    _expect(a, "t1", 0, 18)
    # 19-20: EV_PENDING masked once EV_ENABLE isn't 0 (RX empty): TX enabled shows TX, RX
    # enabled nothing
    for code, (en, want) in enumerate(((1, 1), (2, 0)), start=19):
        a.li("t0", en)
        a.sw("t0", UART_EV_ENABLE, "s0")
        a.lw("t1", UART_EV_PENDING, "s0")
        _expect(a, "t1", want, code)
    a.sw("zero", UART_EV_ENABLE, "s0")
    _puts(a, "UPASS\n")
    a.label("halt")
    a.j("halt")

    _putc_routine(a)
    _fail_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_uart_registers(dut):
    """liteuart's registers: TX only from RXTX; a byte into a full RX FIFO is dropped (RXFULL),
    no read but RXTX's pops; the events are levels"""
    program = uart_regs_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    before = await ser.wait_for(b"?", timeout_cycles=200_000)
    assert before == b"?", f"a store to a register other than RXTX was transmitted: {before!r}"
    ser.write(bytes(range(0x40, 0x61)))  # 33 bytes
    out = (await ser.wait_for(b"\n", timeout_cycles=1_000_000)).decode("ascii", errors="replace")
    assert out.strip() == "UPASS", f"UART register check failed: {out!r} (see uart_regs_program() codes)"


def uart_rx_irq_program() -> list[int]:
    """The external interrupt from the UART's RX event: with MEIE and MIE set, a received byte
    raises nothing until EV_ENABLE has RX; the handler checks mcause and reads the byte."""
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
    # The byte arrives with EV_ENABLE 0: no interrupt
    a.label("rx_wait")
    a.lw("t1", UART_RXEMPTY, "s0")
    a.bnez("t1", "rx_wait")
    for _ in range(4):
        a.nop()
    a.bnez("s1", "early")
    a.li("t0", 2)  # EV_RX
    a.sw("t0", UART_EV_ENABLE, "s0")
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
    a.label("early")
    _puts(a, "EIRQ-EARLY\n")
    a.label("halt5")
    a.j("halt5")

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
    """A received byte raises the machine external interrupt (mcause 0x8000000B) once EV_ENABLE has RX"""
    program = uart_rx_irq_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.write(b"k")
    out = (await ser.wait_for(b"\n", timeout_cycles=1_000_000)).decode("ascii", errors="replace")
    assert out.strip() == "EIRQ-OK", f"UART RX interrupt not taken/handled: {out!r}"


TX_FILL = bytes(range(ord("a"), ord("a") + 33))  # one into the transmitter, 32 fill the FIFO


def uart_tx_irq_program() -> list[int]:
    """The external interrupt from the UART's TX event (the TX FIFO not full): the FIFO filled
    (TXFULL, no TX event, TXEMPTY 0), EV_ENABLE = TX while it is full takes nothing, then the
    interrupt once a byte has left it; the handler counts it and disables TX. TXEMPTY once
    the FIFO has drained. 'F<code>' or 'TXIRQ-OK' after the filling bytes."""
    a = Asm()
    a.la("t0", "handler")
    a.csrw("mtvec", "t0")
    a.li("s0", UART)
    a.li("s1", 0)  # the handler's count
    a.li("t0", 0x800)  # mie.MEIE
    a.csrs("mie", "t0")
    a.li("t0", 0x8)  # mstatus.MIE
    a.csrs("mstatus", "t0")
    for b in TX_FILL:
        a.li("t0", b)
        a.sw("t0", 0, "s0")
    # 1-3: the FIFO full
    a.lw("t1", UART_TXFULL, "s0")
    _expect(a, "t1", 1, 1)
    a.lw("t1", UART_EV_STATUS, "s0")
    _expect(a, "t1", 0, 2)
    a.lw("t1", UART_TXEMPTY, "s0")
    _expect(a, "t1", 0, 3)
    # 4: TX enabled while full: nothing yet (a byte leaves the FIFO a frame later)
    a.li("t0", 1)  # EV_TX
    a.sw("t0", UART_EV_ENABLE, "s0")
    for _ in range(4):
        a.nop()
    _expect(a, "s1", 0, 4)
    # 5: taken once the FIFO has room, exactly once (the handler disables TX)
    a.li("t3", 20000)
    a.label("spin")
    a.bnez("s1", "taken")
    a.addi("t3", "t3", -1)
    a.bnez("t3", "spin")
    a.label("taken")
    for _ in range(20):
        a.nop()
    _expect(a, "s1", 1, 5)
    # 6-7: EV_ENABLE cleared by the handler; TXEMPTY once the FIFO has drained
    a.lw("t1", UART_EV_ENABLE, "s0")
    _expect(a, "t1", 0, 6)
    a.label("drain")
    a.lw("t1", UART_TXEMPTY, "s0")
    a.beqz("t1", "drain")
    a.lw("t1", UART_EV_STATUS, "s0")
    _expect(a, "t1", 1, 7)
    _puts(a, "TXIRQ-OK\n")
    a.label("halt")
    a.j("halt")

    a.label("handler")
    a.csrr("t0", "mcause")
    a.li("t1", 0x8000000B)
    a.bne("t0", "t1", "bad_cause")
    a.addi("s1", "s1", 1)
    a.sw("zero", UART_EV_ENABLE, "s0")
    a.mret()
    a.label("bad_cause")
    a.li("a1", 8)
    a.j("fail")

    _putc_routine(a)
    _fail_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_uart_tx_interrupt(dut):
    """The TX event (the TX FIFO not full) raises the machine external interrupt once EV_ENABLE has TX"""
    program = uart_tx_irq_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    out = await ser.wait_for(b"\n", timeout_cycles=2_000_000)
    assert out == TX_FILL + b"TXIRQ-OK\n", f"UART TX interrupt check failed: {out!r} (see uart_tx_irq_program() codes)"


def counters_program() -> list[int]:
    """cycle/instret/time CSRs and a writable mcycle; 'F<code>' or 'CPASS'."""
    a = Asm()
    a.li("s0", XMEM + 0x300)
    a.li("s1", CLINT_MTIME)
    # 1: instret counts retired instructions, loads/stores once each: the second csrr
    # sees the first csrr, sw, lw and 8 nops retired
    a.csrr("t0", "instret")
    a.sw("t0", 0, "s0")
    a.lw("t2", 0, "s0")
    for _ in range(8):
        a.nop()
    a.csrr("t1", "instret")
    a.sub("t3", "t1", "t0")
    _expect(a, "t3", 11, 1)
    # 2: cycle advances at least as fast as instret over the same kind of window
    a.csrr("t0", "cycle")
    for _ in range(8):
        a.nop()
    a.csrr("t1", "cycle")
    a.sub("t3", "t1", "t0")
    a.li("a1", 2)
    a.li("t2", 9)
    a.bltu("t3", "t2", "fail")
    # 3: time is the timer's mtime: the CSR, read right after the MMIO register, is a few ticks ahead
    a.lw("t0", 0, "s1")
    a.csrr("t1", "time")
    a.sub("t3", "t1", "t0")
    a.li("a1", 3)
    a.li("t2", 32)
    a.bgeu("t3", "t2", "fail")
    # 4: mcycle is writable and keeps counting from the written value
    a.li("t0", 0x1000)
    a.csrw("mcycle", "t0")
    a.csrr("t1", "mcycle")
    a.sub("t3", "t1", "t0")
    a.li("a1", 4)
    a.li("t2", 16)
    a.bgeu("t3", "t2", "fail")
    # 5: cycle reads the same counter
    a.csrr("t1", "cycle")
    a.sub("t3", "t1", "t0")
    a.li("a1", 5)
    a.li("t2", 32)
    a.bgeu("t3", "t2", "fail")
    _puts(a, "CPASS\n")
    a.label("halt")
    a.j("halt")

    _putc_routine(a)
    _fail_routine(a)
    return a.assemble()


@cocotb.test()
async def test_soc_counters(dut):
    """Zicntr: cycle, time (the timer's mtime), instret; mcycle writable"""
    program = counters_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert out.strip() == "CPASS", f"counter check failed: {out!r} (see counters_program() codes)"


@cocotb.test()
async def test_soc_mmio_map(dut):
    """Address decoder, an unassigned region, the ROM's data port, timer, GPIO, UART RX and soft reset"""
    rv32_asm.selftest()
    program = mmio_program()
    ser, _ = await start_soc(dut, btn=0, during_reset=lambda soc: load_imem(soc, program))
    assert int(dut.soc.active_mode.value) == 1, "the power-on reset must start RISC-V"

    await ser.wait_for(b"?", timeout_cycles=200_000)
    ser.write(b"x")
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert "F" not in out, f"MMIO check failed with code {out!r} (see mmio_program() check numbers)"
    assert "SRPASS" in out, f"expected soft-reset marker and PASS, got {out!r}"

    led = int(dut.led.value)
    assert led == (~LED_PATTERN & 0x3F), f"LED pins 0b{led:06b}, expected active-low 0x{LED_PATTERN:02X}"
    check_psram_pins(dut)  # after the window checks (18-22): the real controller's pin protocol
    dut._log.info("SoC memory map and MMIO peripherals verified")


@cocotb.test()
async def test_soc_timer_interrupt(dut):
    """Timer interrupt traps to mtvec with mcause 0x80000007, and mret returns"""
    program = timer_irq_program()
    ser, _ = await start_soc(dut, during_reset=lambda soc: load_imem(soc, program))
    out = (await ser.wait_for(b"\n", timeout_cycles=500_000)).decode("ascii", errors="replace")
    assert out.strip() == "IRQ-OK", f"timer interrupt not taken/handled: {out!r}"
