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

import os
import re
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from lockstep_programs import hash_program
from sdcard_model import SpiSdCardModel
from virtual_serial import VirtualSerialBridge

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
import tools.vux_tool as vux_tool  # noqa: E402 (needs REPO_ROOT on sys.path)

with open(os.path.join(REPO_ROOT, "firmware", "stage1", "src", "main.rs")) as _f:
    _m = re.search(r"const STAGE1_VERSION: u32 = (\d+);", _f.read())
    assert _m, "STAGE1_VERSION not found in main.rs"
    STAGE1_VERSION = int(_m.group(1))
BANNER = f"VUX9K Stage 1 (v{STAGE1_VERSION})"
S1_PROMPT = b"s1> "
S2_PROMPT = b"s2> "
STAGE2_BASE = 0x8070_0000


HASH_PAYLOAD = hash_program(STAGE2_BASE)
# The provisional second stage (make firmware)
STAGE2_RAW = os.path.join(REPO_ROOT, "build", "firmware", "stage2", "stage2.bin")

CLK_PERIOD_PS = 55556  # 18.0 MHz SoC clock (tb_soc_top.sv: 2 x 27778 ps)
UART_BAUD_CYCLES = 156  # 18.0 MHz / 115200 baud
POR_DONE = 1 << 15  # soc_top.por_counter value that ends the ~1.2 ms power-on reset


def mbr_sector() -> bytes:
    """An otherwise empty sector 0 carrying the 0x55AA MBR boot signature."""
    mbr = bytearray(512)
    mbr[510] = 0x55
    mbr[511] = 0xAA
    return bytes(mbr)


def v4_file(payload: bytes = HASH_PAYLOAD, version: int = 1, **kw) -> bytes:
    """A VUX9 v4 file loading at the second stage's address: by default a tiny one that
    prints '#' (RTL reads a card at ~250 cycles a byte: keep what tests boot small)."""
    kw.setdefault("load_addr", STAGE2_BASE)
    data, _ = vux_tool.build_vux9_v4_image(payload, name="test", version=version, **kw)
    return data


def stage2_file(version: int = 1) -> bytes:
    """The provisional second stage (16 KB) as a v4 file, as `make firmware` builds it."""
    with open(STAGE2_RAW, "rb") as f:
        return v4_file(f.read(), version)


def fat_sectors(files: dict) -> dict:
    """A FAT32 card holding files ({"VUX9K/STAGE2.BIN": bytes}) as {lba: sector} for
    start_soc(sd_sectors=..., mbr=False), without its all-zero sectors."""
    img = vux_tool.build_fat32_image(files)
    out = {}
    for lba in range(len(img) // 512):
        sector = img[lba * 512 : (lba + 1) * 512]
        if any(sector):
            out[lba] = sector
    return out


def soc_of(dut):
    """The soc_top instance: dut.soc under sim/tb_soc_top.sv, else dut itself."""
    return dut.soc if hasattr(dut, "soc") else dut


async def wait_cycles(n: int):
    """Wait n board clock cycles with a single Timer callback (unlike ClockCycles)."""
    await Timer(n * CLK_PERIOD_PS, unit="ps")


async def start_soc(dut, *, sd_sectors=None, mbr=True, btn=1, accelerate_por=True, during_reset=None, sd_card=None):
    """Start the clock, attach models, and release reset.

    dut is either sim/tb_soc_top.sv (preferred: clock generated in HDL, SoC at
    dut.soc) or a bare soc_top, in which case the clock is driven from cocotb.

    sd_sectors: {lba: bytes} preloaded into the SD model before reset (after the MBR if mbr=True).
    btn: level of the active-low S2 button during and after reset (0 = held, e.g. for Safe Mode).
    accelerate_por: jump soc_top's POR counter to its end if the signal is visible (RTL only),
        and end the PSRAM word model's power-up with it (Verilator RTL builds), so the window
        is ready from the CPU's first cycle as on the board, where the power-up ends inside
        the POR. The real controller (Icarus) isn't accelerated: a window access waits for it.
    during_reset: optional callable(soc) run while rst_n is still low, e.g. to preload I-RAM.
    sd_card: optional kwargs for SpiSdCardModel (e.g. {"sdhc": False, "strict": True}).

    Returns (ser, sd_model).
    """
    soc = soc_of(dut)
    if soc is dut:
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())

    dut.rst_n.value = 0
    dut.btn.value = btn
    dut.uart_rx.value = 1
    dut.sd_miso.value = 1

    sd_model = SpiSdCardModel(dut.sd_sclk, dut.sd_mosi, dut.sd_miso, dut.sd_cs_n, **(sd_card or {}))
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
        if hasattr(soc.xmem_inst, "init_count"):  # sim/psram_word_model, not the controller
            soc.xmem_inst.init_count.value = 0
    await ClockCycles(soc.clk, 10)
    return ser, sd_model


def check_psram_pins(dut) -> bool:
    """Assert that tb_soc_top's W955D8MBYA pin model counted no protocol/timing error.

    Call it after the test's window accesses. Only Icarus builds have the model (instance
    `psram`, on the real controller's pins); Verilator builds have the word model or nothing
    on the pins, and the check is skipped there. Returns whether it was made.
    """
    if "icarus" not in cocotb.SIM_NAME.lower():
        dut._log.info("No W955D8MBYA pin model in this build (%s): its error count not checked", cocotb.SIM_NAME)
        return False
    errors = int(dut.psram.error_count.value)
    assert errors == 0, f"W955D8MBYA model counted {errors} protocol/timing errors (see the log)"
    return True


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
    if settle_bits:
        await Timer(settle_bits * ser.bit_ps, unit="ps")
    if text:
        ser.reset_input_buffer()
        ser.write(text.encode("utf-8"))
    buf = await ser.wait_for(token, timeout_cycles)
    return buf.decode("utf-8", errors="replace")
