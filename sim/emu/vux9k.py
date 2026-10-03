# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Helpers for tests on the Rust emulator (sim/emu/vux9k.py).

`vux9k_emu` is the pyo3 module that `make emu-py` builds into build/emu/python/.
This module puts it, the repo root (for tools.vux_tool) and sim/integration (for the
pure-Python rv32_asm/hack_asm assemblers) on sys.path, and sets a SoC up the way
sim/integration/soc_env.start_soc() sets up the RTL: firmware preloaded from the
same firmware.hex/firmware_d0-3.hex, an SD card with the MBR signature in sector 0.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EMU_PY_DIR = os.path.join(REPO_ROOT, "build", "emu", "python")
FIRMWARE_DIR = os.path.join(REPO_ROOT, "build", "firmware")
for _p in (EMU_PY_DIR, REPO_ROOT, os.path.join(REPO_ROOT, "sim", "integration")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import vux9k_emu  # noqa: E402
except ImportError as e:  # pragma: no cover - a setup problem, not a test result
    raise ImportError(f"{e}: build the emulator's Python module with `make emu-py`") from e
import tools.vux_tool as vux_tool  # noqa: E402

Soc = vux9k_emu.Soc

CLK_HZ = 18_000_000
UART_BIT = 156  # clocks per bit at 115200 baud
UART_FRAME = 10 * UART_BIT
SECTOR = 512


def ms(n: float) -> int:
    """Clock cycles in n milliseconds."""
    return int(CLK_HZ * n / 1000)


def mbr_sector() -> bytes:
    """An otherwise empty sector 0 carrying the 0x55AA MBR boot signature."""
    mbr = bytearray(SECTOR)
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


def slot_lba(slot: int) -> int:
    """First SD sector of a program slot (same formula as the firmware and vux_tool)."""
    return 64 + (slot << 6)


def slot_image(payload: bytes, *, slot: int, mode: str, name: str = ""):
    """VUX9 v3 image for slot as {lba: sector bytes}, plus vux_tool's meta."""
    raw, meta = vux_tool.build_vux9_image(payload, slot=slot, name=name, mode=mode)
    base = slot_lba(slot)
    sectors = {base + i: raw[i * SECTOR : (i + 1) * SECTOR] for i in range(meta["num_sectors"])}
    return sectors, meta


def sd_image(sectors: dict, *, mbr: bool = True) -> bytes:
    """Raw SD image from {lba: bytes} (sector 0 = MBR signature unless mbr=False)."""
    sectors = ({0: mbr_sector()} if mbr else {}) | dict(sectors)
    if not sectors:
        return b""
    img = bytearray(SECTOR * (max(sectors) + 1))
    for lba, data in sectors.items():
        img[lba * SECTOR : lba * SECTOR + len(data[:SECTOR])] = data[:SECTOR]
    return bytes(img)


def read_hex_words(path) -> list[int]:
    """One 32-bit hex word per line (scripts/bin2hex.py / elf2bin.py output)."""
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


def preload_firmware(soc) -> None:
    """soc_ram's power-on contents: build/firmware/firmware.hex and firmware_d0-3.hex."""
    texts = []
    for name in ["firmware.hex"] + [f"firmware_d{i}.hex" for i in range(4)]:
        path = os.path.join(FIRMWARE_DIR, name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} missing; run `make firmware` first")
        with open(path) as f:
            texts.append(f.read())
    soc.load_readmemh(*texts)


# Firmware coverage (make coverage-fw): with VUX9K_COV_DIR set, every SoC created
# through start_soc() records the instructions it executes, and the union is written
# to <dir>/<pid>.cov (hex keys, one per line) when the process exits.
_COV_DIR = os.environ.get("VUX9K_COV_DIR")
_cov_socs: list = []


def _write_coverage():
    assert _COV_DIR
    keys = set()
    for soc in _cov_socs:
        keys.update(soc.cov_keys())
    os.makedirs(_COV_DIR, exist_ok=True)
    with open(os.path.join(_COV_DIR, f"{os.getpid()}.cov"), "w") as f:
        f.writelines(f"{k:x}\n" for k in sorted(keys))


if _COV_DIR:
    import atexit

    atexit.register(_write_coverage)


def new_soc(profile="real"):
    """A Soc that records coverage when VUX9K_COV_DIR is set."""
    soc = Soc(profile)
    if _COV_DIR:
        soc.cov_enable()
        _cov_socs.append(soc)
    return soc


def start_soc(*, profile="real", sd_sectors=None, mbr=True, sd_card=None, card=True, imem_words=None):
    """A SoC right after power-on reset, like soc_env.start_soc() on the RTL.

    sd_sectors: {lba: bytes} on the card (after the MBR if mbr=True).
    sd_card: keyword arguments for Soc.sd_insert (sdhc, strict, faults).
    card: False leaves the socket empty.
    imem_words: 32-bit words written over I-RAM from word 0 after the preload.
    """
    soc = new_soc(profile)
    preload_firmware(soc)
    if imem_words is not None:
        soc.load_iram_words(list(imem_words))
    if card:
        soc.sd_insert(sd_image(sd_sectors or {}, mbr=mbr), **(sd_card or {}))
    return soc


def wait_for(soc, token: bytes, timeout_cycles: int, start: int = 0) -> str:
    """Run until the UART output from index start contains token; return that output.

    Raises TimeoutError with the output so far, like VirtualSerialBridge.wait_for.
    """
    end = soc.run_until_tx(token, timeout_cycles, start)
    out = soc.uart_received()
    if end is None:
        raise TimeoutError(f"{token!r} not received within {timeout_cycles} cycles; got {out[start:]!r}")
    return out[start:end].decode("utf-8", errors="replace")


def send_paced(soc, data: bytes, chunk: int = 16, drain_cycles: int = 50_000_000) -> None:
    """Send data in chunks, each once the program has emptied the RX FIFO.

    The FIFO holds 32 bytes; a program that only polls the UART between longer
    computations (bc, the Boot Manager) needs a host that doesn't outrun it, as a
    person typing does. Raises if the FIFO overflowed anyway.
    """
    for i in range(0, len(data), chunk):
        soc.uart_send(data[i : i + chunk])
        soc.run(max(0, soc.uart_send_done - soc.cycle))
        end = soc.cycle + drain_cycles
        while soc.uart_rx_level and soc.cycle < end:
            soc.run(2_000)
    if soc.uart_overrun:
        raise RuntimeError("UART RX overrun: the program didn't keep up with the host")


def press_button(soc, hold_ms: float = 25, after_ms: float = 15) -> None:
    """Press S2 for hold_ms and release it, then run after_ms (like test_soc_fast)."""
    soc.set_button(True)
    soc.run(ms(hold_ms))
    soc.set_button(False)
    soc.run(ms(after_ms))


class EmuClock:
    """Stand-in for the `time` module in emulated time (18 MHz cycles), for host
    code such as tools/vux_tool.py: sleep() runs the SoC, time() reads its clock.
    Install with monkeypatch.setattr(vux_tool, "time", EmuClock(soc))."""

    def __init__(self, soc):
        self.soc = soc

    def time(self) -> float:
        return self.soc.cycle / CLK_HZ

    monotonic = time

    def sleep(self, seconds: float) -> None:
        self.soc.run(max(1, int(seconds * CLK_HZ)))


class EmuSerial:
    """A pyserial-like port on the emulator's UART (what vux_tool.py uses of it).

    write() puts bytes on the RX line back to back at 115200 baud, as a USB-UART
    does; read(n) runs the SoC until something arrives or `timeout` seconds of
    emulated time pass, and returns what the host has completely received.
    """

    def __init__(self, soc, timeout: float = 0.2):
        self.soc = soc
        self.timeout = timeout
        self._pos = len(soc.uart_received())
        self.log = bytearray()  # everything read, for diagnostics

    def _available(self) -> int:
        return len(self.soc.uart_received()) - self._pos

    @property
    def in_waiting(self) -> int:
        return self._available()

    def write(self, data: bytes) -> int:
        self.soc.uart_send(bytes(data))
        return len(data)

    def flush(self) -> None:
        self.soc.run(max(0, self.soc.uart_send_done - self.soc.cycle))

    def read(self, size: int = 1) -> bytes:
        end = self.soc.cycle + int((self.timeout or 0) * CLK_HZ)
        while self._available() == 0 and self.soc.cycle < end:
            self.soc.run(UART_FRAME)
        got = self.soc.uart_received()[self._pos : self._pos + size]
        self._pos += len(got)
        self.log += got
        return bytes(got)

    def reset_input_buffer(self) -> None:
        self._pos = len(self.soc.uart_received())

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        pass
