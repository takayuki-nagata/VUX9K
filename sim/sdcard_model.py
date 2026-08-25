# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Cocotb Virtual SD Card SPI Slave Model
Simulates SD Card SPI mode for RTL simulation and verification.
Supports CMD0, CMD8, CMD55, ACMD41, CMD17 (Read), and CMD24 (Write).
"""

import cocotb
from cocotb.triggers import RisingEdge, FallingEdge

class SpiSdCardModel:
    def __init__(self, sclk_sig, mosi_sig, miso_sig, cs_n_sig):
        self.sclk = sclk_sig
        self.mosi = mosi_sig
        self.miso = miso_sig
        self.cs_n = cs_n_sig
        self.sectors = {} # lba -> bytearray(512)
        self.in_app_cmd = False
        self.is_ready = False
        self.miso.value = 1

    def preload_sector(self, lba: int, data: bytes):
        """Preload binary data into a sector"""
        buf = bytearray(512)
        buf[:len(data)] = data[:512]
        self.sectors[lba] = buf

    def get_sector(self, lba: int) -> bytes:
        return bytes(self.sectors.get(lba, bytearray(512)))

    async def run(self):
        """Main SPI slave coroutine"""
        while True:
            # Wait for CS Low
            if self.cs_n.value != 0:
                await FallingEdge(self.cs_n)

            # Receive 6-byte command (0x40 | cmd, arg[31:24], arg[23:16], arg[15:8], arg[7:0], crc)
            cmd_bytes = await self._recv_bytes(6)
            if not cmd_bytes or self.cs_n.value != 0:
                continue

            cmd = cmd_bytes[0] & 0x3F
            arg = (cmd_bytes[1] << 24) | (cmd_bytes[2] << 16) | (cmd_bytes[3] << 8) | cmd_bytes[4]

            if cmd == 0: # CMD0: GO_IDLE_STATE
                self.is_ready = False
                await self._send_byte(0xFF) # 8-clock delay
                await self._send_byte(0x01) # R1: In Idle State

            elif cmd == 8: # CMD8: SEND_IF_COND
                await self._send_byte(0xFF)
                await self._send_byte(0x01) # R1: In Idle State
                # R7 payload: 0x00, 0x00, 0x01, 0xAA
                await self._send_bytes(bytes([0x00, 0x00, 0x01, 0xAA]))

            elif cmd == 55: # CMD55: APP_CMD
                self.in_app_cmd = True
                await self._send_byte(0xFF)
                await self._send_byte(0x01 if not self.is_ready else 0x00)

            elif cmd == 41: # ACMD41: SD_SEND_OP_COND
                self.is_ready = True
                self.in_app_cmd = False
                await self._send_byte(0xFF)
                await self._send_byte(0x00) # R1: Ready (0x00)

            elif cmd == 17: # CMD17: READ_SINGLE_BLOCK
                lba = arg
                sector_data = self.sectors.get(lba, bytearray(512))
                await self._send_byte(0xFF)
                await self._send_byte(0x00) # R1: Success
                await self._send_byte(0xFF) # Inter-token gap
                await self._send_byte(0xFE) # Data Token
                await self._send_bytes(sector_data) # 512 bytes payload
                await self._send_bytes(bytes([0x12, 0x34])) # 2 bytes CRC

            elif cmd == 24: # CMD24: WRITE_SINGLE_BLOCK
                lba = arg
                await self._send_byte(0xFF)
                await self._send_byte(0x00) # R1: Success
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
