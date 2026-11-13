# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
SD card SPI-mode protocol at byte level (sim/integration/sdcard_protocol.py).

Pure Python, no cocotb: for every byte the SPI master clocks, the card is asked for
its MISO byte (`begin_byte`) and then given the MOSI byte and the CS level
(`end_byte`). sdcard_model.py drives it bit by bit from the RTL's pins; the Rust
emulator's card (emu/crates/vux9k_emu/src/sdcard.rs) implements the same rules, and
the transcripts in sim/sd_transcripts/ hold both to identical MISO bytes.

Answers (the model's behavior, not a full SD implementation):
- CMD0 FF 01; CMD8 FF 01 00 00 01 AA; CMD55 FF 01 (00 once ready); ACMD41 and
  CMD1 (MMC) FF 00 (CMD41 also without CMD55);
  CMD58 FF 00 + OCR (C0 SDHC | 80 SDSC) FF 80 00; CMD16 FF 00; CMD59 (CRC_ON_OFF)
  FF 00; CMD13 (SEND_STATUS) R2 FF 00 00; CMD12 outside a multiple-block read FF 00;
  others: no answer. A command right after CMD55 is an application command (ACMD13,
  ACMD51; ACMD41 as CMD41).
- Data blocks: FF FE, the data, and its CRC16 (CCITT, the SD spec's data CRC): CMD17
  FF 00 + a 512-byte block; CMD9 (CSD) and CMD10 (CID) FF 00 + 16 bytes; ACMD51 (SCR)
  FF 00 + 8 bytes; ACMD13 (SD status) R2 FF 00 00 + 64 bytes. The CSD is version 2
  with the capacity of the sectors held (`csd`); its command classes leave out switch
  (CMD6) and erase.
- CMD18 FF 00, then one block after the other from consecutive sectors, while the card
  keeps reading MOSI: CMD12 stops it (FF 00 after it: a stuff byte, R1). CS going high
  stops it too, dropping the rest of the block.
- CMD24 FF 00, then data token FE, 512 bytes + 2 CRC; data response 05 and a busy
  byte 3F. The card then listens for a new command only after CS has gone high.
  CMD25 FF 00, then blocks with token FC, each answered 05 3F, until the stop token FD
  (answered FF 00: N(BR) and one busy byte); then the card listens for commands.
- CRC: CMD59 with bit 0 set turns it on. A written block whose CRC16 is wrong is then
  refused (data response 0B), and strict mode records a command whose CRC7 is wrong.
- Answers are sent whatever CS does, and while answering the card ignores MOSI (but
  not during CMD18). CS going high drops a partial command, a wait for a data token or
  a partial block.

Default (sdhc=True, strict=False) is deliberately forgiving: CMD17/CMD24 arguments
>= 0x10000 are taken as byte offsets. sdhc=False makes an SDSC card (OCR CCS=0,
byte addresses); strict=True applies the card type's addressing exactly and records
`violations`: an SDSC byte address that isn't a multiple of 512, fewer than 74
SCLK cycles with CS high before the first command (SD spec power-up sequence), and
SCLK above 400 kHz before the card is ready (from power-up or CMD0 until ACMD41/CMD1
answers 00), reported once until the card is ready. The host tells the card its SCLK with
`set_sclk_hz` (default: INIT_SCLK_HZ).

Faults (for firmware error paths): mute_cmds (never answered), never_ready (ACMD41
keeps answering 01), read_error (CMD17/CMD18 send data error token 08; CMD18 then
stops), bad_sectors (the same, for those sectors only), write_reject
(data response 0B, block not written).
"""

SECTOR = 512
POWER_UP_CLOCKS = 74  # SD spec: >= 74 clocks with CS high before the first command
INIT_SCLK_HZ = 400_000  # SD spec: the most SCLK until the card leaves identification


def crc7(data: bytes) -> int:
    """CRC7 of SD commands and registers (x^7 + x^3 + 1), MSB first."""
    crc = 0
    for b in data:
        for i in range(7, -1, -1):
            bit = ((b >> i) & 1) ^ ((crc >> 6) & 1)
            crc = (crc << 1) & 0x7F
            if bit:
                crc ^= 0x09
    return crc


def crc16(data: bytes) -> int:
    """CRC16 of SD data blocks (CCITT x^16 + x^12 + x^5 + 1, initial 0), MSB first."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


# SCR: structure 0, SD spec 2.00, security SDHC, bus widths 1 and 4
SCR = bytes([0x02, 0x35, 0, 0, 0, 0, 0, 0])


def cid() -> bytes:
    """CID: manufacturer 0x56, OEM "VX", product "VUX9K", revision 1.0, serial 1, 2026-11."""
    c = bytes([0x56]) + b"VXVUX9K" + bytes([0x10, 0, 0, 0, 1, 0x01, 0xAB])
    return c + bytes([crc7(c) << 1 | 1])


class SdCardProtocol:
    def __init__(
        self,
        *,
        sdhc=True,
        strict=False,
        mute_cmds=(),
        never_ready=False,
        read_error=False,
        bad_sectors=(),
        write_reject=False,
    ):
        self.sdhc = sdhc
        self.strict = strict
        self.mute_cmds = set(mute_cmds)
        self.never_ready = never_ready
        self.read_error = read_error
        self.bad_sectors = set(bad_sectors)
        self.write_reject = write_reject
        self.sectors = {}  # lba -> bytes(512)
        self.commands = []  # (cmd, arg) log
        self.violations = []  # human-readable protocol problems (strict mode)
        self.idle_clocks = 0  # SCLK cycles with CS high before the first command
        self._first_cmd_seen = False
        self._ready = False
        self.sclk_hz = INIT_SCLK_HZ
        self._fast_reported = False  # a too-fast SCLK was reported while not ready
        self._resp = bytearray()
        self._answering = False  # the current byte comes from _resp
        self._state = "idle"  # idle | cmd | wait_token | data | read_stream | resync
        self._buf = bytearray()
        self._lba = 0
        self._multi = False  # wait_token/data: CMD25 (else CMD24)
        self._app = False  # the previous command was CMD55
        self._crc_on = False  # CMD59

    # --- storage ----------------------------------------------------------------
    def get_sector(self, lba: int) -> bytes:
        return bytes(self.sectors.get(lba, bytes(SECTOR)))

    def set_sector(self, lba: int, data: bytes):
        self.sectors[lba] = bytes(data[:SECTOR]).ljust(SECTOR, b"\x00")

    def csd(self) -> bytes:
        """CSD version 2.0 for the capacity of the sectors held (the Rust card: its image),
        with its CRC7. C_SIZE counts 512 KB units, rounded up; command classes 0, 2, 4, 7, 8."""
        sectors = max(self.sectors) + 1 if self.sectors else 0
        c_size = max(-(-sectors // 1024), 1) - 1
        c = bytes(
            [0x40, 0x0E, 0x00, 0x32, 0x19, 0x59, 0x00]
            + [(c_size >> 16) & 0x3F, (c_size >> 8) & 0xFF, c_size & 0xFF]
            + [0x7F, 0x80, 0x0A, 0x40, 0x00]
        )
        return c + bytes([crc7(c) << 1 | 1])

    # --- bus ----------------------------------------------------------------------
    def set_cs(self, selected: bool):
        """CS changed (True = selected)."""
        if not selected:
            if self._state == "read_stream":
                self._resp = bytearray()
            self._state = "idle"
            self._buf = bytearray()

    def set_sclk_hz(self, hz: int):
        """SCLK of the bytes clocked from now on."""
        self.sclk_hz = hz

    def begin_byte(self) -> int:
        """MISO byte for the next byte clocked (it can't depend on that byte's MOSI)."""
        if not self._resp and self._state == "read_stream":
            lba = self._lba
            self._lba = (lba + 1) & 0xFFFF_FFFF
            if not self._read_block(lba):
                self._state = "idle"
        self._answering = bool(self._resp)
        if self._answering:
            return self._resp.pop(0)
        return 0xFF

    def end_byte(self, mosi: int, selected: bool):
        """The byte clocked: its MOSI and whether CS was low during it."""
        if not selected and not self._first_cmd_seen:
            self.idle_clocks += 8
        if self.strict and not self._ready and self.sclk_hz > INIT_SCLK_HZ and not self._fast_reported:
            self._fast_reported = True
            self.violations.append(
                f"SCLK {self.sclk_hz} Hz before the card is ready, SD spec allows <= {INIT_SCLK_HZ} Hz"
            )
        if self._state == "read_stream":
            if selected:
                if self._buf or mosi & 0xC0 == 0x40:
                    self._buf.append(mosi)
                if len(self._buf) == 6:
                    buf, self._buf = bytes(self._buf), bytearray()
                    if buf[0] & 0x3F == 12:
                        self._resp = bytearray()
                        self._state = "idle"
                        self._command(buf)
            return
        if self._answering or not selected:
            return
        if self._state == "idle":
            if mosi & 0xC0 == 0x40:
                self._state, self._buf = "cmd", bytearray([mosi])
        elif self._state == "cmd":
            self._buf.append(mosi)
            if len(self._buf) == 6:
                self._state = "idle"
                self._command(bytes(self._buf))
        elif self._state == "wait_token":
            if mosi == (0xFC if self._multi else 0xFE):
                self._state, self._buf = "data", bytearray()
            elif self._multi and mosi == 0xFD:
                self._state = "idle"
                self._resp += b"\xff\x00"
        elif self._state == "data":
            self._buf.append(mosi)
            if len(self._buf) == SECTOR + 2:
                data = bytes(self._buf)
                bad_crc = self._crc_on and int.from_bytes(data[SECTOR:], "big") != crc16(data[:SECTOR])
                lba = self._lba
                if self._multi:
                    self._state, self._lba = "wait_token", (lba + 1) & 0xFFFF_FFFF
                else:
                    self._state = "resync"
                if self.write_reject or bad_crc:
                    self._resp += b"\x0b"
                else:
                    self.set_sector(lba, data[:SECTOR])
                    self._resp += b"\x05\x3f"

    def exchange(self, mosi: int, selected: bool) -> int:
        """One whole byte: returns MISO."""
        miso = self.begin_byte()
        self.end_byte(mosi, selected)
        return miso

    # --- commands -------------------------------------------------------------------
    def _addr_lba(self, cmd: int, arg: int) -> int:
        if not self.strict:
            return arg if arg < 0x10000 else arg >> 9
        if self.sdhc:
            return arg
        if arg % SECTOR:
            self.violations.append(f"CMD{cmd}: SDSC byte address 0x{arg:X} is not sector-aligned (block address sent?)")
        return arg >> 9

    def _data_block(self, data: bytes):
        self._resp += b"\xff\xfe" + data + crc16(data).to_bytes(2, "big")

    def _read_block(self, lba: int) -> bool:
        """Queue sector `lba` as a data block, or the error token FF 08 (faults)."""
        if self.read_error or lba in self.bad_sectors:
            self._resp += b"\xff\x08"
            return False
        self._data_block(self.get_sector(lba))
        return True

    def _command(self, buf: bytes):
        cmd = buf[0] & 0x3F
        arg = int.from_bytes(buf[1:5], "big")
        app, self._app = self._app, False
        self.commands.append((cmd, arg))
        want = crc7(buf[:5]) << 1 | 1
        if self.strict and self._crc_on and buf[5] != want:
            self.violations.append(f"CMD{cmd}: CRC7 byte 0x{buf[5]:02X}, expected 0x{want:02X} (CRC on)")
        if not self._first_cmd_seen:
            self._first_cmd_seen = True
            if self.strict and self.idle_clocks < POWER_UP_CLOCKS:
                self.violations.append(
                    f"power-up: only {self.idle_clocks} SCLK cycles with CS high before the first command "
                    f"(CMD{cmd}), SD spec requires >= {POWER_UP_CLOCKS}"
                )
        if cmd in self.mute_cmds:
            return
        r = self._resp
        if cmd == 0:  # GO_IDLE_STATE
            self._ready = False
            r += b"\xff\x01"
        elif cmd == 8:  # SEND_IF_COND: R1 + R7 echo
            r += b"\xff\x01\x00\x00\x01\xaa"
        elif cmd == 55:  # APP_CMD
            self._app = True
            r += b"\xff\x00" if self._ready else b"\xff\x01"
        elif cmd in (1, 41):  # SEND_OP_COND (CMD1, MMC) / SD_SEND_OP_COND (ACMD41)
            self._ready = not self.never_ready
            self._fast_reported = self._fast_reported and not self._ready
            r += b"\xff\x00" if self._ready else b"\xff\x01"
        elif cmd == 58:  # READ_OCR: powered up, CCS for SDHC
            r += bytes([0xFF, 0x00, 0xC0 if self.sdhc else 0x80, 0xFF, 0x80, 0x00])
        elif cmd in (16, 12):  # SET_BLOCKLEN; STOP_TRANSMISSION outside CMD18
            r += b"\xff\x00"
        elif cmd == 59:  # CRC_ON_OFF
            self._crc_on = bool(arg & 1)
            r += b"\xff\x00"
        elif cmd == 13 and app:  # SD_STATUS: R2 + 64 bytes
            r += b"\xff\x00\x00"
            self._data_block(bytes(64))
        elif cmd == 13:  # SEND_STATUS: R2
            r += b"\xff\x00\x00"
        elif cmd == 51 and app:  # SEND_SCR
            r += b"\xff\x00"
            self._data_block(SCR)
        elif cmd == 9:  # SEND_CSD
            r += b"\xff\x00"
            self._data_block(self.csd())
        elif cmd == 10:  # SEND_CID
            r += b"\xff\x00"
            self._data_block(cid())
        elif cmd == 17:  # READ_SINGLE_BLOCK
            lba = self._addr_lba(cmd, arg)
            r += b"\xff\x00"
            self._read_block(lba)
        elif cmd == 18:  # READ_MULTIPLE_BLOCK
            lba = self._addr_lba(cmd, arg)
            r += b"\xff\x00"
            if self._read_block(lba):
                self._state, self._lba, self._buf = "read_stream", (lba + 1) & 0xFFFF_FFFF, bytearray()
        elif cmd in (24, 25):  # WRITE_SINGLE_BLOCK, WRITE_MULTIPLE_BLOCK
            self._lba = self._addr_lba(cmd, arg)
            self._multi = cmd == 25
            r += b"\xff\x00"
            self._state = "wait_token"
