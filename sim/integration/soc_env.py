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
from lockstep_programs import HACK_I_BASE, HACK_STUB_FLAG, hack_image_bytes, hack_stub, hash_program
from rv32_asm import Asm
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
# The PSRAM's start (fw_common::map::XMEM_BASE): where RV32 applications are linked (the
# Zephyr board vux9k) and the second stage's `run` starts them
XMEM_BASE = 0x8000_0000
XMEM_HALF_WORDS = 1 << 20  # psram_word_model's mem is [2][XMEM_WORDS / 2] (soc_pkg::XMEM_WORDS = 2^21)


HASH_PAYLOAD = hash_program(STAGE2_BASE)
# The second stage (make firmware): raw, and the v4 file for the card
STAGE2_RAW = os.path.join(REPO_ROOT, "build", "firmware", "stage2", "stage2.bin")
STAGE2_BIN = os.path.join(REPO_ROOT, "build", "firmware", "stage2", "STAGE2.BIN")
with open(os.path.join(REPO_ROOT, "firmware", "stage2", "src", "main.rs")) as _f:
    _m = re.search(r"const STAGE2_VERSION: u32 = (\d+);", _f.read())
    assert _m, "STAGE2_VERSION not found in main.rs"
    STAGE2_VERSION = int(_m.group(1))
S2_BANNER = f"VUX9K Stage 2 (v{STAGE2_VERSION})"
# The demos as v4 files for \VUX9K\APPS\ (make apps)
APPS_DIR = os.path.join(REPO_ROOT, "build", "firmware", "apps")
# Power-on to the real second stage's prompt: the first stage's key window (1.8M cycles) and
# its read of the 35 KB STAGE2.BIN from the card (~12.8M cycles in all on the emulator)
S2_BOOT_CYCLES = 20_000_000

CLK_PERIOD_PS = 55556  # 18.0 MHz SoC clock (tb_soc_top.sv: 2 x 27778 ps)
UART_BAUD_CYCLES = 156  # 18.0 MHz / 115200 baud
POR_DONE = 1 << 15  # soc_top.por_counter value that ends the ~1.2 ms power-on reset
# Cycles the window's caches take to clear their lines (soc_icache's LINES, one a cycle) while
# the power-on reset counts: accelerate_por lets them pass before it ends the POR
CACHE_CLEAR_CYCLES = 4096


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
    """The second stage (35 KB) as a v4 file, as `make firmware` builds it."""
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


def s2_card(*apps: str) -> dict:
    """fat_sectors() of a card as the dist lays it out: the real second stage as
    \\VUX9K\\STAGE2.BIN and the named demos of APPS_DIR (e.g. "HELLO.BIN") in
    \\VUX9K\\APPS\\."""
    files = {}
    paths = [("VUX9K/STAGE2.BIN", STAGE2_BIN)] + [(f"VUX9K/APPS/{a}", os.path.join(APPS_DIR, a)) for a in apps]
    for dest, path in paths:
        assert os.path.exists(path), f"{path} missing; run `make firmware apps`"
        with open(path, "rb") as f:
            files[dest] = f.read()
    return fat_sectors(files)


async def s2_cmd(ser, line: str, until: bytes = S2_PROMPT, timeout_cycles: int = 4_000_000) -> str:
    """A command line at the second stage's prompt; returns its echo and what it printed
    up to `until`."""
    return await send_and_wait(ser, line + "\r", until, timeout_cycles)


async def boot_s2(dut, *apps: str, **kw):
    """Power on with s2_card(*apps) and run to the second stage's prompt (start_soc's
    keyword arguments in kw); returns (ser, output)."""
    ser, _ = await start_soc(dut, sd_sectors=s2_card(*apps), **kw)
    out = await send_and_wait(ser, "", S2_PROMPT, timeout_cycles=S2_BOOT_CYCLES, settle_bits=0)
    assert S2_BANNER in out, out
    return ser, out


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
        once the window's caches have cleared their lines (CACHE_CLEAR_CYCLES), and end the
        PSRAM word model's power-up with it (Verilator RTL builds), so the window is ready
        from the CPU's first cycle as on the board, where both end inside the POR. The real
        controller (Icarus) isn't accelerated: a window access waits for it.
    during_reset: optional callable(soc) run while rst_n is still low, e.g. to preload the ROM.
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
        await wait_cycles(CACHE_CLEAR_CYCLES)
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
    """Overwrite the boot ROM (soc_rom) from word 0, through VPI: a test's program in place
    of the first stage (the CPU can't write it). Call it while the CPU is held in reset."""
    for idx, word in enumerate(words):
        soc.rom_inst.mem[idx].value = word


def load_xmem(soc, addr: int, image: bytes):
    """Write image into the PSRAM at RV32 address addr (word-aligned; the last word's bytes
    past the end 0): the word model's words, one VPI write each, so only Verilator RTL
    builds, which have sim/psram_word_model in place of the controller. Call it while the
    CPU is held in reset."""
    assert addr % 4 == 0 and XMEM_BASE <= addr and addr + len(image) <= XMEM_BASE + 2 * XMEM_HALF_WORDS * 4
    assert hasattr(soc.xmem_inst, "mem"), "no PSRAM word model in this build (Verilator RTL only)"
    mem = soc.xmem_inst.mem
    first = (addr - XMEM_BASE) // 4
    for i in range(0, len(image), 4):
        word = first + i // 4
        mem[word // XMEM_HALF_WORDS][word % XMEM_HALF_WORDS].value = int.from_bytes(
            image[i : i + 4].ljust(4, b"\0"), "little"
        )


def jump_words(target: int) -> list[int]:
    """ROM words that jump to target (lui t0; jalr x0, 0(t0)): placed at 0 in place of
    the first stage, they start an application the way the second stage's `run` does,
    without the boot. target must be 4 KB-aligned (lui's reach)."""
    assert target % 4096 == 0, hex(target)
    a = Asm()
    a.lui("t0", target >> 12)
    a.jalr("zero", "t0", 0)
    return a.assemble()


def preload_psram_app(image: bytes, addr: int = XMEM_BASE):
    """start_soc's during_reset for an RV32 application linked into the PSRAM at addr: the
    image in the PSRAM (load_xmem) and a jump to it at ROM word 0 (jump_words)."""

    def preload(soc):
        load_xmem(soc, addr, image)
        load_imem(soc, jump_words(addr))

    return preload


# Cycles from start_soc's return to the CPU in Hack: hack_stub's PSRAM load and store, the
# GPIO stores and the 15-cycle soft-reset pulse (~150), with room to spare
HACK_START_CYCLES = 400


async def start_hack(dut, words, **kw):
    """start_soc for a Hack program: its words (two instructions each, the first in the low
    half: hack_asm, scripts/bin2hex.py) in Hack's I segment (load_xmem: Verilator RTL builds
    only) and lockstep_programs.hack_stub at ROM word 0, which gives Hack in GPIO 0x8 and
    soft-resets into it, as a loader does (the power-on reset starts RV32); when the program
    soft-resets back without a mode, the stub prints HACK_BACK and spins. kw goes to
    start_soc. Returns (ser, sd_model) once the CPU runs the program in Hack."""
    image = hack_image_bytes(list(words))

    def preload(soc):
        load_imem(soc, hack_stub())
        load_xmem(soc, HACK_I_BASE, image)
        load_xmem(soc, HACK_STUB_FLAG, bytes(4))  # the stub's first pass

    ser, sd_model = await start_soc(dut, during_reset=preload, **kw)
    await wait_cycles(HACK_START_CYCLES)
    mode = int(soc_of(dut).active_mode.value)
    assert mode == 0, f"hack_stub's soft reset must start Hack (active_mode=0), got {mode}"
    return ser, sd_model


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
