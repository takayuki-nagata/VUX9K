#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
VUX9K Host Tooling (vux_tool.py)
Provides UART <-> SPI bridge communication, SD card sector flashing,
hardware auto-load triggers, and integrated serial terminal monitoring.
"""

import sys
import os
import time
import argparse

try:
    import serial
except ImportError:
    serial = None

try:
    import pyftdi.serialext
except ImportError:
    pyftdi = None

class VuxBridge:
    def __init__(self, port="auto", baud=115200, timeout=1.0):
        if port.startswith("ftdi://"):
            if pyftdi is None:
                raise RuntimeError("pyftdi is not installed! Please run 'uv pip install pyftdi'.")
            self.ser = pyftdi.serialext.serial_for_url(port, baudrate=baud, timeout=timeout)
        elif port == "auto":
            # First try pyftdi with FT2232 Channel B (common for Tang Nano 9K)
            connected = False
            if pyftdi is not None:
                try:
                    self.ser = pyftdi.serialext.serial_for_url("ftdi://ftdi:2232/2", baudrate=baud, timeout=timeout)
                    connected = True
                except Exception:
                    pass
            if not connected:
                if serial is None:
                    raise RuntimeError("Neither pyftdi nor pyserial could open port.")
                for p in ["/dev/ttyUSB3", "/dev/ttyUSB1", "/dev/ttyUSB0"]:
                    try:
                        self.ser = serial.Serial(p, baudrate=baud, timeout=timeout)
                        connected = True
                        break
                    except Exception:
                        continue
            if not connected:
                raise RuntimeError("Could not open FTDI / Serial port automatically!")
        else:
            if serial is None:
                raise RuntimeError("pyserial is not installed!")
            self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self.timeout = timeout

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def sync(self, retries=5):
        """Send Sync byte (0x5A) and wait for ACK (0x5A)"""
        for i in range(retries):
            self.ser.write(bytes([0x5A]))
            self.ser.flush()
            resp = self.ser.read(1)
            if resp and resp[0] == 0x5A:
                return True
            time.sleep(0.05)
        return False

    def spi_xfer_byte(self, byte_val):
        """Send 1 byte over SPI via bridge and receive 1 byte"""
        self.ser.write(bytes([0x01, byte_val & 0xFF]))
        resp = self.ser.read(1)
        if not resp:
            raise TimeoutError("Bridge SPI transfer timeout!")
        return resp[0]

    def spi_xfer(self, data: bytes) -> bytes:
        """Transfer multiple bytes over SPI"""
        res = bytearray()
        for b in data:
            res.append(self.spi_xfer_byte(b))
        return bytes(res)

    def set_cs(self, active: bool):
        """Control SD Card CS line (active = Low, inactive = High)"""
        cmd = 0x02 if active else 0x03
        self.ser.write(bytes([cmd]))
        resp = self.ser.read(1)
        if not resp or resp[0] != 0x06:
            raise RuntimeError(f"Failed to set CS line (active={active})")

    def trigger_boot(self):
        """Trigger SD Card Auto-Load and CPU execution"""
        self.ser.write(bytes([0x05]))
        self.ser.flush()

    def trigger_direct_boot(self):
        """Skip SD load and release CPU reset directly"""
        self.ser.write(bytes([0x06]))
        self.ser.flush()

class SdCardFlasher:
    def __init__(self, bridge: VuxBridge):
        self.bridge = bridge

    def init_sd(self) -> bool:
        """Initialize SD Card in SPI mode (CMD0 -> CMD8 -> ACMD41)"""
        print("[VUX9K] Initializing SD Card in SPI mode...")
        # 80 dummy clocks with CS High
        self.bridge.set_cs(False)
        self.bridge.spi_xfer(b'\xFF' * 10)

        # CMD0: GO_IDLE_STATE
        self.bridge.set_cs(True)
        resp = self._send_cmd(0, 0x00000000, 0x95)
        if resp != 0x01:
            print(f"[VUX9K] SD Card CMD0 failed, response: 0x{resp:02X}")
            self.bridge.set_cs(False)
            return False

        # CMD8: SEND_IF_COND (check 2.7-3.6V range)
        resp = self._send_cmd(8, 0x000001AA, 0x87)
        if resp == 0x01:
            # Read 4 bytes return
            r7 = self.bridge.spi_xfer(b'\xFF' * 4)
            if r7[2:] != b'\x01\xAA':
                print("[VUX9K] SD Card CMD8 voltage mismatch!")
                self.bridge.set_cs(False)
                return False

        # ACMD41: Initialize card
        ready = False
        for _ in range(100):
            # CMD55 (APP_CMD)
            self._send_cmd(55, 0x00000000, 0x65)
            # ACMD41 (HCS = 1)
            resp = self._send_cmd(41, 0x40000000, 0x77)
            if resp == 0x00:
                ready = True
                break
            time.sleep(0.01)

        self.bridge.set_cs(False)
        if not ready:
            print("[VUX9K] SD Card ACMD41 timeout!")
            return False

        print("[VUX9K] SD Card successfully initialized in SPI Mode! [OK]")
        return True

    def _send_cmd(self, cmd: int, arg: int, crc: int) -> int:
        cmd_bytes = bytes([
            0x40 | cmd,
            (arg >> 24) & 0xFF,
            (arg >> 16) & 0xFF,
            (arg >> 8) & 0xFF,
            arg & 0xFF,
            crc & 0xFF
        ])
        self.bridge.spi_xfer(cmd_bytes)
        # Wait for R1 response (MSB is 0)
        for _ in range(64):
            r = self.bridge.spi_xfer_byte(0xFF)
            if (r & 0x80) == 0:
                return r
        return 0xFF

    def write_sector(self, lba: int, data: bytes) -> bool:
        """Write single 512-byte sector to SD card (CMD24)"""
        if len(data) != 512:
            raise ValueError(f"Sector data must be exactly 512 bytes, got {len(data)}")

        self.bridge.set_cs(True)
        resp = self._send_cmd(24, lba, 0xFF)
        if resp != 0x00:
            print(f"[VUX9K] CMD24 failed at LBA {lba}, response: 0x{resp:02X}")
            self.bridge.set_cs(False)
            return False

        # Send Data Token 0xFE
        self.bridge.spi_xfer_byte(0xFE)
        # Send 512 bytes payload
        self.bridge.spi_xfer(data)
        # Send 2 bytes dummy CRC
        self.bridge.spi_xfer(b'\xFF\xFF')

        # Check Data Response (xxx00101b = 0x05: Data accepted)
        data_resp = self.bridge.spi_xfer_byte(0xFF)
        if (data_resp & 0x1F) != 0x05:
            print(f"[VUX9K] Data rejected at LBA {lba}, response: 0x{data_resp:02X}")
            self.bridge.set_cs(False)
            return False

        # Wait while busy (MISO held Low)
        for _ in range(5000):
            if self.bridge.spi_xfer_byte(0xFF) == 0xFF:
                self.bridge.set_cs(False)
                self.bridge.spi_xfer_byte(0xFF) # 8 trailing clocks
                return True

        print(f"[VUX9K] Write busy timeout at LBA {lba}!")
        self.bridge.set_cs(False)
        return False

    def flash_binary(self, bin_path: str, start_lba: int = 64) -> bool:
        """Flash binary file into SD Card starting at start_lba (default: LBA 64 = 32KB MBR gap)"""
        if not os.path.exists(bin_path):
            print(f"Error: File {bin_path} not found!")
            return False

        with open(bin_path, "rb") as f:
            data = f.read()

        total_bytes = len(data)
        num_sectors = (total_bytes + 511) // 512
        offset_kb = (start_lba * 512) // 1024
        print(f"[VUX9K] Flashing {bin_path} ({total_bytes} bytes, {num_sectors} sectors) to SD Card @ LBA {start_lba} ({offset_kb} KB offset in MBR gap)...")

        if not self.init_sd():
            return False

        for sec in range(num_sectors):
            offset = sec * 512
            chunk = data[offset:offset+512]
            if len(chunk) < 512:
                chunk = chunk + b'\x00' * (512 - len(chunk))

            if not self.write_sector(start_lba + sec, chunk):
                print(f"[VUX9K] Failed writing sector {start_lba + sec}!")
                return False

            pct = (sec + 1) * 100 // num_sectors
            sys.stdout.write(f"\r[VUX9K] Progress: [{sec + 1}/{num_sectors}] {pct}%")
            sys.stdout.flush()

        print("\n[VUX9K] Firmware flash completed successfully! [OK]")
        return True

def monitor_serial(port: str, baud: int):
    """Simple serial console monitor"""
    print(f"[VUX9K] Starting serial monitor on {port} @ {baud} baud (Press Ctrl+C to exit)...")
    try:
        if port.startswith("ftdi://") or port == "auto":
            if pyftdi is not None:
                url = port if port.startswith("ftdi://") else "ftdi://ftdi:2232/2"
                ser = pyftdi.serialext.serial_for_url(url, baudrate=baud, timeout=0.1)
            else:
                ser = serial.Serial("/dev/ttyUSB1", baudrate=baud, timeout=0.1)
        else:
            ser = serial.Serial(port, baudrate=baud, timeout=0.1)
        while True:
            data = ser.read(128)
            if data:
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n[VUX9K] Monitor terminated by user.")
    except Exception as e:
        print(f"\n[VUX9K] Serial error: {e}")

def main():
    parser = argparse.ArgumentParser(description="VUX9K SoC Host Tooling & Flasher")
    parser.add_argument("--port", default="auto", help="Serial port (default: auto)")
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate (default: 115200)")
    parser.add_argument("--flash", help="Path to firmware.bin to flash to SD card and boot")
    parser.add_argument("--lba", type=int, default=64, help="Starting LBA sector on SD Card (default: 64 = 32KB MBR gap)")
    parser.add_argument("--boot", action="store_true", help="Trigger SD Auto-Load and CPU boot")
    parser.add_argument("--direct-boot", action="store_true", help="Release CPU reset directly")
    parser.add_argument("--monitor", action="store_true", help="Open serial console monitor")

    args = parser.parse_args()

    if not any([args.flash, args.boot, args.direct_boot, args.monitor]):
        parser.print_help()
        sys.exit(1)

    bridge = None
    try:
        if args.flash or args.boot or args.direct_boot:
            print(f"[VUX9K] Connecting to VUX9K Bridge on {args.port}...")
            bridge = VuxBridge(port=args.port, baud=args.baud)
            if not bridge.sync():
                print("[VUX9K] Could not synchronize with Hardware Boot Manager. (Is FPGA configured and reset?)")
                sys.exit(1)
            print("[VUX9K] Connected to Hardware Boot Manager in Bridge Mode [OK]")

            if args.flash:
                flasher = SdCardFlasher(bridge)
                if not flasher.flash_binary(args.flash, start_lba=args.lba):
                    sys.exit(1)
                print("[VUX9K] Booting CPU from SD Card...")
                bridge.trigger_boot()
                bridge.close()
                bridge = None
                monitor_serial(args.port, args.baud)
                return

            if args.boot:
                print("[VUX9K] Triggering SD Auto-Load & Boot...")
                bridge.trigger_boot()
                bridge.close()
                bridge = None
                monitor_serial(args.port, args.baud)
                return

            if args.direct_boot:
                print("[VUX9K] Triggering Direct CPU Boot...")
                bridge.trigger_direct_boot()
                bridge.close()
                bridge = None
                monitor_serial(args.port, args.baud)
                return

        if args.monitor:
            monitor_serial(args.port, args.baud)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        if bridge:
            bridge.close()

if __name__ == "__main__":
    main()
