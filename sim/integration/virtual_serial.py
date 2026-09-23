# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Virtual Serial Interface for Cocotb Simulations (sim/virtual_serial.py)
High-performance event-driven UART receiver and transmitter.
"""

import collections
import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, Event


def _get_bit(val):
    try:
        return int(val) & 1
    except (ValueError, TypeError):
        return 1


class VirtualSerialBridge:
    def __init__(self, dut, baud_cycles=234, rx_pin="uart_tx", tx_pin="uart_rx", clk_pin="clk"):
        self.dut = dut
        self.baud_cycles = baud_cycles
        self.rx_pin = getattr(dut, rx_pin)
        self.tx_pin = getattr(dut, tx_pin)
        self.clk_pin = getattr(dut, clk_pin)
        
        self.rx_buffer = collections.deque()
        self.tx_buffer = collections.deque()
        self.tx_idle = True
        self._tx_event = Event()
        
        # Default pin state
        self.tx_pin.value = 1
        
        # Start background workers
        self._rx_task = cocotb.start_soon(self._rx_worker())
        self._tx_task = cocotb.start_soon(self._tx_worker())

    async def _rx_worker(self):
        """Continuously receive bytes from DUT uart_tx using FallingEdge event triggers"""
        while True:
            # If line is currently 0, wait until it returns to idle 1
            if _get_bit(self.rx_pin.value) == 0:
                while _get_bit(self.rx_pin.value) == 0:
                    await ClockCycles(self.clk_pin, self.baud_cycles // 4)
            
            # Wait for start bit (falling edge on rx_pin: 1 -> 0)
            await FallingEdge(self.rx_pin)
            
            # Center of start bit (0.5 baud period)
            await ClockCycles(self.clk_pin, self.baud_cycles // 2)
            if _get_bit(self.rx_pin.value) != 0:
                continue
            
            # Sample 8 data bits (LSB first)
            byte_val = 0
            for i in range(8):
                await ClockCycles(self.clk_pin, self.baud_cycles)
                byte_val |= (_get_bit(self.rx_pin.value) << i)
            
            # Append decoded byte immediately
            self.rx_buffer.append(byte_val)
            
            # Wait to middle of stop bit (0.5 baud) so FallingEdge is armed before next byte
            await ClockCycles(self.clk_pin, self.baud_cycles // 2)

    async def _tx_worker(self):
        """Transmit bytes from tx_buffer to DUT uart_rx"""
        while True:
            if not self.tx_buffer:
                self.tx_idle = True
                self._tx_event.clear()
                await self._tx_event.wait()
            
            self.tx_idle = False
            b = self.tx_buffer.popleft()
            
            # Start bit (0)
            self.tx_pin.value = 0
            await ClockCycles(self.clk_pin, self.baud_cycles)
            
            # 8 Data bits (LSB first)
            for i in range(8):
                self.tx_pin.value = (b >> i) & 1
                await ClockCycles(self.clk_pin, self.baud_cycles)
            
            # Stop bit (1)
            self.tx_pin.value = 1
            await ClockCycles(self.clk_pin, self.baud_cycles)
            # Inter-byte gap
            await ClockCycles(self.clk_pin, self.baud_cycles)

    def write(self, data: bytes):
        """Enqueue bytes for transmission"""
        for b in data:
            if isinstance(b, int):
                self.tx_buffer.append(b)
            else:
                self.tx_buffer.append(ord(b))
        self._tx_event.set()

    def read(self, size: int = 1) -> bytes:
        """Read up to `size` bytes from receive buffer synchronously"""
        out = bytearray()
        while len(out) < size and self.rx_buffer:
            out.append(self.rx_buffer.popleft())
        return bytes(out)

    def reset_input_buffer(self):
        """Clear received bytes"""
        self.rx_buffer.clear()

    def reset_output_buffer(self):
        """Clear transmit queue"""
        self.tx_buffer.clear()

    def flush(self):
        """No-op for compatibility"""
        pass

    @property
    def in_waiting(self) -> int:
        return len(self.rx_buffer)
