# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb Virtual SD Card SPI Slave Model (sim/integration/sdcard_model.py).

Drives the byte-level card of sdcard_protocol.py (answers, SDHC/SDSC, strict mode,
faults: see there) from the RTL's SPI pins: MISO changes after each falling SCLK
edge, MOSI and CS are sampled on rising edges, and every 8 rising edges make one byte.
Each byte's SCLK frequency (its first to last rising edge) goes to the card, which
checks it during initialization in strict mode.
Keyword arguments go to SdCardProtocol (e.g. sdhc=False, strict=True).
"""

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import FallingEdge, RisingEdge
from sdcard_protocol import POWER_UP_CLOCKS, SdCardProtocol  # noqa: F401 (re-exported)


class SpiSdCardModel:
    def __init__(self, sclk_sig, mosi_sig, miso_sig, cs_n_sig, **card):
        self.sclk = sclk_sig
        self.mosi = mosi_sig
        self.miso = miso_sig
        self.cs_n = cs_n_sig
        self.card = SdCardProtocol(**card)
        self.miso.value = 1

    @property
    def violations(self):
        return self.card.violations

    @property
    def commands(self):
        return self.card.commands

    @property
    def idle_clocks(self) -> int:
        """SCLK cycles seen with CS high before the first command (power-up preamble)."""
        return self.card.idle_clocks

    def preload_sector(self, lba: int, data: bytes):
        """Preload binary data into a sector"""
        self.card.set_sector(lba, data)

    def get_sector(self, lba: int) -> bytes:
        return self.card.get_sector(lba)

    async def _watch_cs(self):
        while True:
            await self.cs_n.value_change
            self.card.set_cs(self.cs_n.value == 0)

    async def run(self):
        """Main SPI slave coroutine: one iteration per byte clocked."""
        cocotb.start_soon(self._watch_cs())
        while True:
            miso = self.card.begin_byte()
            mosi = 0
            selected = False
            for i in range(7, -1, -1):
                self.miso.value = (miso >> i) & 1
                await RisingEdge(self.sclk)
                if i == 7:
                    selected = self.cs_n.value == 0
                    first_rise = get_sim_time("ps")
                elif i == 0:
                    self.card.set_sclk_hz(round(7e12 / (get_sim_time("ps") - first_rise)))
                bit = int(self.mosi.value) if self.mosi.value.is_resolvable else 1
                mosi = (mosi << 1) | bit
                await FallingEdge(self.sclk)
            self.card.end_byte(mosi, selected)
