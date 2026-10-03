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
  CMD1 (MMC) FF 00;
  CMD58 FF 00 + OCR (C0 SDHC | 80 SDSC) FF 80 00; CMD16 FF 00; others: no answer.
- CMD17 FF 00 FF FE, 512 data bytes, CRC 12 34.
- CMD24 FF 00, then data token FE, 512 bytes + 2 CRC; data response 05 and a busy
  byte 3F. The card then listens for a new command only after CS has gone high.
- Answers are sent whatever CS does, and while answering the card ignores MOSI.
  CS going high drops a partial command, a wait for a data token or a partial block.

Default (sdhc=True, strict=False) is deliberately forgiving: CMD17/CMD24 arguments
>= 0x10000 are taken as byte offsets. sdhc=False makes an SDSC card (OCR CCS=0,
byte addresses); strict=True applies the card type's addressing exactly and records
`violations`: an SDSC byte address that isn't a multiple of 512, fewer than 74
SCLK cycles with CS high before the first command (SD spec power-up sequence), and
SCLK above 400 kHz before the card is ready (from power-up or CMD0 until ACMD41/CMD1
answers 00), reported once until the card is ready. The host tells the card its SCLK with
`set_sclk_hz` (default: INIT_SCLK_HZ).

Faults (for firmware error paths): mute_cmds (never answered), never_ready (ACMD41
keeps answering 01), read_error (CMD17 sends data error token 08), bad_sectors
(the same, for those sectors only), write_reject
(data response 0B, block not written).
"""

SECTOR = 512
POWER_UP_CLOCKS = 74  # SD spec: >= 74 clocks with CS high before the first command
INIT_SCLK_HZ = 400_000  # SD spec: the most SCLK until the card leaves identification


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
        self._state = "idle"  # idle | cmd | wait_token | data | resync
        self._buf = bytearray()
        self._lba = 0

    # --- storage ----------------------------------------------------------------
    def get_sector(self, lba: int) -> bytes:
        return bytes(self.sectors.get(lba, bytes(SECTOR)))

    def set_sector(self, lba: int, data: bytes):
        self.sectors[lba] = bytes(data[:SECTOR]).ljust(SECTOR, b"\x00")

    # --- bus ----------------------------------------------------------------------
    def set_cs(self, selected: bool):
        """CS changed (True = selected)."""
        if not selected:
            self._state = "idle"
            self._buf = bytearray()

    def set_sclk_hz(self, hz: int):
        """SCLK of the bytes clocked from now on."""
        self.sclk_hz = hz

    def begin_byte(self) -> int:
        """MISO byte for the next byte clocked (it can't depend on that byte's MOSI)."""
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
        if self._answering or not selected:
            return
        if self._state == "idle":
            if mosi & 0xC0 == 0x40:
                self._state, self._buf = "cmd", bytearray([mosi])
        elif self._state == "cmd":
            self._buf.append(mosi)
            if len(self._buf) == 6:
                cmd = self._buf[0] & 0x3F
                arg = int.from_bytes(self._buf[1:5], "big")
                self._state = "idle"
                self._command(cmd, arg)
        elif self._state == "wait_token":
            if mosi == 0xFE:
                self._state, self._buf = "data", bytearray()
        elif self._state == "data":
            self._buf.append(mosi)
            if len(self._buf) == SECTOR + 2:
                self._state = "resync"
                if self.write_reject:
                    self._resp += b"\x0b"
                else:
                    self.set_sector(self._lba, bytes(self._buf[:SECTOR]))
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

    def _command(self, cmd: int, arg: int):
        self.commands.append((cmd, arg))
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
            r += b"\xff\x00" if self._ready else b"\xff\x01"
        elif cmd in (1, 41):  # SEND_OP_COND (CMD1, MMC) / SD_SEND_OP_COND (ACMD41)
            self._ready = not self.never_ready
            self._fast_reported = self._fast_reported and not self._ready
            r += b"\xff\x00" if self._ready else b"\xff\x01"
        elif cmd == 58:  # READ_OCR: powered up, CCS for SDHC
            r += bytes([0xFF, 0x00, 0xC0 if self.sdhc else 0x80, 0xFF, 0x80, 0x00])
        elif cmd == 16:  # SET_BLOCKLEN
            r += b"\xff\x00"
        elif cmd == 17:  # READ_SINGLE_BLOCK
            lba = self._addr_lba(cmd, arg)
            if self.read_error or lba in self.bad_sectors:
                r += b"\xff\x00\xff\x08"
            else:
                r += b"\xff\x00\xff\xfe" + self.get_sector(lba) + b"\x12\x34"
        elif cmd == 24:  # WRITE_SINGLE_BLOCK
            self._lba = self._addr_lba(cmd, arg)
            r += b"\xff\x00"
            self._state = "wait_token"
