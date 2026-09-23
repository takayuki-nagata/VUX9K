# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Shared soc_top testbench setup (sim/integration/soc_env.py).

Clock, external pin defaults, SD card model, UART bridge, active-low reset and
simulated POR acceleration, in the one order every SoC test needs. Long waits
use Timer / the bridge's event-driven wait_for(), never ClockCycles: cocotb's
ClockCycles resumes Python on every clock edge, which dominates the runtime of
multi-million-cycle SoC tests.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from sdcard_model import SpiSdCardModel
from virtual_serial import VirtualSerialBridge

CLK_PERIOD_PS = 37038  # 27.0 MHz board clock
UART_BAUD_CYCLES = 234  # 27.0 MHz / 115200 baud
POR_DONE = 1 << 15  # soc_top.por_counter value that ends the ~1.2 ms power-on reset


def mbr_sector() -> bytes:
    """An otherwise empty sector 0 carrying the 0x55AA MBR boot signature."""
    mbr = bytearray(512)
    mbr[510] = 0x55
    mbr[511] = 0xAA
    return bytes(mbr)


def soc_of(dut):
    """The soc_top instance: dut.soc under sim/tb_soc_top.sv, else dut itself."""
    return dut.soc if hasattr(dut, "soc") else dut


async def wait_cycles(n: int):
    """Wait n board clock cycles with a single Timer callback (unlike ClockCycles)."""
    await Timer(n * CLK_PERIOD_PS, unit="ps")


async def start_soc(dut, *, sd_sectors=None, mbr=True, btn=1, accelerate_por=True, during_reset=None):
    """Start the clock, attach models, and release reset.

    dut is either sim/tb_soc_top.sv (preferred: clock generated in HDL, SoC at
    dut.soc) or a bare soc_top, in which case the clock is driven from cocotb.

    sd_sectors: {lba: bytes} preloaded into the SD model before reset (after the MBR if mbr=True).
    btn: level of the active-low S2 button during and after reset (0 = held, e.g. for Safe Mode).
    accelerate_por: jump soc_top's POR counter to its end if the signal is visible (RTL only).
    during_reset: optional callable(soc) run while rst_n is still low, e.g. to preload I-RAM.

    Returns (ser, sd_model).
    """
    soc = soc_of(dut)
    if soc is dut:
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())

    dut.rst_n.value = 0
    dut.btn.value = btn
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n)
    cocotb.start_soon(sd_model.run())
    if mbr:
        sd_model.preload_sector(0, mbr_sector())
    for lba, data in (sd_sectors or {}).items():
        sd_model.preload_sector(lba, data)

    ser = VirtualSerialBridge(dut, baud_cycles=UART_BAUD_CYCLES, clk_period_ps=CLK_PERIOD_PS)

    await ClockCycles(soc.clk, 10)
    if during_reset is not None:
        during_reset(soc)
    dut.rst_n.value = 1
    await ClockCycles(soc.clk, 5)
    if accelerate_por and hasattr(soc, "por_counter"):
        soc.por_counter.value = POR_DONE
    await ClockCycles(soc.clk, 10)
    return ser, sd_model


def load_imem(soc, words):
    """Overwrite soc_ram's I-RAM from word 0 (call while the CPU is held in reset)."""
    for idx, word in enumerate(words):
        soc.ram_inst.i_mem[idx].value = word


def read_hex_words(path) -> list[int]:
    """One 32-bit hex word per line, as produced by scripts/bin2hex.py / elf2bin.py."""
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


async def send_and_wait(ser, text: str, token: bytes, timeout_cycles: int, settle_bits: int = 50) -> str:
    """Pause settle_bits UART bit times, send text (if any), and wait for token.

    The receive buffer is only discarded when something is actually sent: when
    waiting for an unprompted follow-up (e.g. "[READY-SEC:0]" right after
    "[READY-COUNT:N]"), resetting it would clip the start of the reply.
    """
    await Timer(settle_bits * ser.bit_ps, unit="ps")
    if text:
        ser.reset_input_buffer()
        ser.write(text.encode("utf-8"))
    buf = await ser.wait_for(token, timeout_cycles)
    return buf.decode("utf-8", errors="replace")
