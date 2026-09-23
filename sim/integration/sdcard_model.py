# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb Virtual SD Card SPI Slave Model
Simulates SD Card SPI mode for RTL simulation and verification.
Supports CMD0, CMD8, CMD55, ACMD41, CMD58, CMD16, CMD17 (Read), and CMD24 (Write).

Default (sdhc=True, strict=False) is deliberately forgiving: an SDHC card that also
accepts byte addresses (CMD17/CMD24 args >= 0x10000 are treated as byte offsets).
Two opt-in knobs model real-card behavior the default hides:
- sdhc=False: an SDSC card -- OCR CCS=0, and CMD17/CMD24 take a *byte* address.
- strict=True: addresses are interpreted exactly as the card type requires (no
  guessing), and protocol problems are recorded in `violations`: a byte address that
  isn't a multiple of 512 on SDSC, and fewer than 74 SCLK cycles with CS high between
  power-up (model start) and the first command (SD spec power-up sequence).
"""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge

POWER_UP_CLOCKS = 74  # SD spec: >= 74 clocks with CS high before the first command


class SpiSdCardModel:
    def __init__(self, sclk_sig, mosi_sig, miso_sig, cs_n_sig, *, sdhc=True, strict=False):
        self.sclk = sclk_sig
        self.mosi = mosi_sig
        self.miso = miso_sig
        self.cs_n = cs_n_sig
        self.sdhc = sdhc
        self.strict = strict
        self.sectors = {}  # lba -> bytearray(512)
        self.in_app_cmd = False
        self.is_ready = False
        self.violations = []  # human-readable protocol problems (strict mode)
        self.commands = []  # (cmd, arg) log
        self._idle_clocks = 0  # SCLK cycles with CS high before the first command
        self._first_cmd_seen = False
        self.miso.value = 1

    @property
    def idle_clocks(self) -> int:
        """SCLK cycles seen with CS high before the first command (power-up preamble)."""
        return self._idle_clocks

    def _lba(self, cmd: int, arg: int) -> int:
        """Sector addressed by a CMD17/CMD24 argument."""
        if not self.strict:
            return arg if arg < 0x10000 else (arg >> 9)
        if self.sdhc:
            return arg
        if arg % 512:
            self.violations.append(f"CMD{cmd}: SDSC byte address 0x{arg:X} is not sector-aligned (block address sent?)")
        return arg >> 9

    async def _count_power_up_clocks(self):
        while not self._first_cmd_seen:
            await RisingEdge(self.sclk)
            if self.cs_n.value == 1 and not self._first_cmd_seen:
                self._idle_clocks += 1

    def preload_sector(self, lba: int, data: bytes):
        """Preload binary data into a sector"""
        buf = bytearray(512)
        buf[: len(data)] = data[:512]
        self.sectors[lba] = buf

    def get_sector(self, lba: int) -> bytes:
        return bytes(self.sectors.get(lba, bytearray(512)))

    async def run(self):
        """Main SPI slave coroutine"""
        cocotb.start_soon(self._count_power_up_clocks())
        while True:
            # Wait for CS Low
            if self.cs_n.value != 0:
                await FallingEdge(self.cs_n)

            # Receive command: wait for start byte (01xxxxxx)
            first_byte = 0xFF
            while (first_byte & 0xC0) != 0x40 and self.cs_n.value == 0:
                first_byte = await self._recv_byte()
            if self.cs_n.value != 0 or (first_byte & 0xC0) != 0x40:
                continue

            rest_bytes = await self._recv_bytes(5)
            if len(rest_bytes) < 5 or self.cs_n.value != 0:
                continue
            cmd_bytes = bytes([first_byte]) + rest_bytes

            cmd = cmd_bytes[0] & 0x3F
            arg = (cmd_bytes[1] << 24) | (cmd_bytes[2] << 16) | (cmd_bytes[3] << 8) | cmd_bytes[4]
            self.commands.append((cmd, arg))
            if not self._first_cmd_seen:
                self._first_cmd_seen = True
                if self.strict and self._idle_clocks < POWER_UP_CLOCKS:
                    self.violations.append(
                        f"power-up: only {self._idle_clocks} SCLK cycles with CS high before the first command "
                        f"(CMD{cmd}), SD spec requires >= {POWER_UP_CLOCKS}"
                    )

            if cmd == 0:  # CMD0: GO_IDLE_STATE
                self.is_ready = False
                await self._send_byte(0xFF)  # 8-clock delay
                await self._send_byte(0x01)  # R1: In Idle State

            elif cmd == 8:  # CMD8: SEND_IF_COND
                await self._send_byte(0xFF)
                await self._send_byte(0x01)  # R1: In Idle State
                # R7 payload: 0x00, 0x00, 0x01, 0xAA
                await self._send_bytes(bytes([0x00, 0x00, 0x01, 0xAA]))

            elif cmd == 55:  # CMD55: APP_CMD
                self.in_app_cmd = True
                await self._send_byte(0xFF)
                await self._send_byte(0x01 if not self.is_ready else 0x00)

            elif cmd == 41:  # ACMD41: SD_SEND_OP_COND
                self.is_ready = True
                self.in_app_cmd = False
                await self._send_byte(0xFF)
                await self._send_byte(0x00)  # R1: Ready (0x00)

            elif cmd == 58:  # CMD58: READ_OCR
                await self._send_byte(0xFF)
                await self._send_byte(0x00)  # R1: Success
                # OCR: Powered Up (bit 31) and CCS (bit 30) = 1 for SDHC, 0 for SDSC
                await self._send_bytes(bytes([0xC0 if self.sdhc else 0x80, 0xFF, 0x80, 0x00]))

            elif cmd == 16:  # CMD16: SET_BLOCKLEN
                await self._send_byte(0xFF)
                await self._send_byte(0x00)  # R1: Success

            elif cmd == 17:  # CMD17: READ_SINGLE_BLOCK
                lba = self._lba(cmd, arg)
                sector_data = self.sectors.get(lba, bytearray(512))
                await self._send_byte(0xFF)
                await self._send_byte(0x00)  # R1: Success
                await self._send_byte(0xFF)  # Inter-token gap
                await self._send_byte(0xFE)  # Data Token
                await self._send_bytes(sector_data)  # 512 bytes payload
                await self._send_bytes(bytes([0x12, 0x34]))  # 2 bytes CRC

            elif cmd == 24:  # CMD24: WRITE_SINGLE_BLOCK
                lba = self._lba(cmd, arg)
                await self._send_byte(0xFF)
                await self._send_byte(0x00)  # R1: Success
                # Wait for Data Token 0xFE
                tok = 0xFF
                while tok != 0xFE and self.cs_n.value == 0:
                    tok = await self._recv_byte()

                if tok == 0xFE:
                    payload = await self._recv_bytes(512)
                    _crc = await self._recv_bytes(2)
                    self.sectors[lba] = bytearray(payload)
                    # Send Data Response: 0x05 (Data Accepted)
                    await self._send_byte(0x05)
                    # Send Busy signal (Low) then High
                    self.miso.value = 0
                    await RisingEdge(self.sclk)
                    await RisingEdge(self.sclk)
                    self.miso.value = 1

    async def _recv_byte(self) -> int:
        val = 0
        for _ in range(8):
            await RisingEdge(self.sclk)
            bit = int(self.mosi.value) if self.mosi.value.is_resolvable else 1
            val = (val << 1) | bit
            await FallingEdge(self.sclk)
        return val

    async def _recv_bytes(self, count: int) -> bytes:
        res = bytearray()
        for _ in range(count):
            if self.cs_n.value != 0:
                break
            res.append(await self._recv_byte())
        return bytes(res)

    async def _send_byte(self, byte_val: int):
        val = byte_val & 0xFF
        for i in range(7, -1, -1):
            self.miso.value = (val >> i) & 1
            await RisingEdge(self.sclk)
            await FallingEdge(self.sclk)
        self.miso.value = 1

    async def _send_bytes(self, data: bytes):
        for b in data:
            await self._send_byte(b)
