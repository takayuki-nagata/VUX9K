# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Virtual Serial Interface for Cocotb Simulations (sim/integration/virtual_serial.py)
Event-driven 8N1 UART receiver/transmitter with a pyserial-like interface.

All waiting is done with Timer / edge / Event triggers, never with ClockCycles:
cocotb's ClockCycles(clk, n) resumes Python on every one of the n clock edges,
which made Python (not the simulator) the bottleneck of long SoC tests. Here a
UART bit costs one Timer callback, and wait_for() sleeps until a byte arrives.
"""

import collections

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import Event, FallingEdge, First, RisingEdge, Timer

DEFAULT_CLK_PERIOD_PS = 55556  # 18.0 MHz, matches the SoC testbenches' clock


def _get_bit(val):
    try:
        return int(val) & 1
    except (ValueError, TypeError):
        return 1


class VirtualSerialBridge:
    def __init__(
        self,
        dut,
        baud_cycles=156,
        rx_pin="uart_tx",
        tx_pin="uart_rx",
        clk_pin="clk",
        clk_period_ps=DEFAULT_CLK_PERIOD_PS,
    ):
        self.dut = dut
        self.baud_cycles = baud_cycles
        self.clk_period_ps = clk_period_ps
        self.bit_ps = baud_cycles * clk_period_ps
        self.rx_pin = getattr(dut, rx_pin)
        self.tx_pin = getattr(dut, tx_pin)
        self.clk_pin = getattr(dut, clk_pin)

        self.rx_buffer: collections.deque[int] = collections.deque()
        self.tx_buffer: collections.deque[int] = collections.deque()
        self.tx_idle = True
        self._tx_event = Event()
        self._rx_event = Event()

        # Default pin state
        self.tx_pin.value = 1

        # Start background workers
        self._rx_task = cocotb.start_soon(self._rx_worker())
        self._tx_task = cocotb.start_soon(self._tx_worker())

    async def _bits(self, n):
        await Timer(n * self.bit_ps, unit="ps")

    async def _rx_worker(self):
        """Continuously receive bytes from DUT uart_tx"""
        while True:
            # If line is currently 0, wait until it returns to idle 1
            if _get_bit(self.rx_pin.value) == 0:
                await RisingEdge(self.rx_pin)

            # Wait for start bit (falling edge on rx_pin: 1 -> 0)
            await FallingEdge(self.rx_pin)

            # Center of start bit (0.5 baud period)
            await Timer(self.bit_ps // 2, unit="ps")
            if _get_bit(self.rx_pin.value) != 0:
                continue

            # Sample 8 data bits (LSB first)
            byte_val = 0
            for i in range(8):
                await self._bits(1)
                byte_val |= _get_bit(self.rx_pin.value) << i

            # Append decoded byte immediately
            self.rx_buffer.append(byte_val)
            self._rx_event.set()

            # Wait to middle of stop bit (0.5 baud) so FallingEdge is armed before next byte
            await Timer(self.bit_ps // 2, unit="ps")

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
            await self._bits(1)

            # 8 Data bits (LSB first)
            for i in range(8):
                self.tx_pin.value = (b >> i) & 1
                await self._bits(1)

            # Stop bit (1) + inter-byte gap
            self.tx_pin.value = 1
            await self._bits(2)

    async def _consume_until(self, done, timeout_cycles: int, what: str, log_every_cycles: int):
        """Consume received bytes until done(buf) is true or the timeout expires.

        Sleeps on the receive event (no polling). Returns (buf, done(buf)).
        """
        buf = b""
        deadline = get_sim_time("ps") + timeout_cycles * self.clk_period_ps
        next_log = get_sim_time("ps") + log_every_cycles * self.clk_period_ps
        while True:
            if self.rx_buffer:
                buf += self.read(len(self.rx_buffer))
                if done(buf):
                    return buf, True
            now = get_sim_time("ps")
            if now >= deadline:
                return buf, False
            if now >= next_log:
                cocotb.log.info(f"Waiting for {what}; received so far: {buf[-60:]!r}")
                next_log = now + log_every_cycles * self.clk_period_ps
            self._rx_event.clear()
            await First(self._rx_event.wait(), Timer(min(deadline, next_log) - now, unit="ps"))

    async def wait_for(self, token: bytes, timeout_cycles: int, log_every_cycles: int = 2_000_000) -> bytes:
        """Consume received bytes until `token` has been seen; return everything consumed.

        Raises TimeoutError if the token hasn't arrived within `timeout_cycles` clock cycles.
        """
        buf, found = await self._consume_until(lambda b: token in b, timeout_cycles, repr(token), log_every_cycles)
        if not found:
            raise TimeoutError(f"Timeout waiting for {token!r}. Received: {buf.decode('utf-8', errors='replace')!r}")
        return buf

    async def wait_any(self, timeout_cycles: int, log_every_cycles: int = 2_000_000) -> bytes:
        """Return as soon as at least one byte has been received (b"" on timeout, no exception)."""
        buf, _ = await self._consume_until(lambda b: len(b) > 0, timeout_cycles, "any byte", log_every_cycles)
        return buf

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
