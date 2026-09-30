# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
sdcard_spi (SPI master for the MicroSD card) unit tests.

Register map (addr[3:0]): 0x0 write = start an 8-bit transfer, read = last received
byte; 0x4 chip select (bit 0 = cs_n); 0x8 busy (ro). SPI mode 0: SCLK idles low,
MISO is sampled on the rising edge (through a 2-flop synchronizer), MOSI shifts on
the falling edge, MSB first. SCLK half-period = CLK_DIV_HALF (23) system clocks.
Writes are only accepted while not busy -- including chip-select writes.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

CLK_DIV_HALF = 23  # soc_pkg::SD_CLK_DIV_HALF at 18 MHz
BYTE_CYCLES = 8 * 2 * CLK_DIV_HALF


class SpiSlave:
    """Mode-0 slave: captures MOSI on SCLK rising edges, shifts `reply` out MSB first."""

    def __init__(self, dut):
        self.dut = dut
        self.rx_bits = []
        self.rise_times = []
        self.reply = 0xFF

    def load(self, reply):
        self.reply = reply
        self.rx_bits = []
        self.rise_times = []
        self.dut.spi_miso.value = (reply >> 7) & 1

    async def run(self):
        bit = 0
        while True:
            await RisingEdge(self.dut.spi_sclk)
            self.rx_bits.append(int(self.dut.spi_mosi.value))
            self.rise_times.append(cocotb.simtime.get_sim_time("ns"))
            await FallingEdge(self.dut.spi_sclk)
            bit = len(self.rx_bits)
            if bit < 8:
                self.dut.spi_miso.value = (self.reply >> (7 - bit)) & 1

    def received(self):
        value = 0
        for b in self.rx_bits:
            value = (value << 1) | b
        return value


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.data_in.value = 0
    dut.spi_miso.value = 1
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    slave = SpiSlave(dut)
    cocotb.start_soon(slave.run())
    return slave


async def write(dut, addr, value):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    dut.data_in.value = value
    dut.we.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0


async def read(dut, addr):
    await FallingEdge(dut.clk)
    dut.addr.value = addr
    await FallingEdge(dut.clk)
    return int(dut.data_out.value)


async def wait_idle(dut, max_cycles=4 * BYTE_CYCLES):
    for _ in range(max_cycles):
        if await read(dut, 0x8) == 0:
            return
    raise TimeoutError("sdcard_spi stayed busy")


@cocotb.test()
async def test_reset_state_and_cs(dut):
    """Idle state after reset, and chip select is software controlled"""
    await setup(dut)
    assert int(dut.spi_cs_n.value) == 1 and int(dut.spi_sclk.value) == 0 and int(dut.spi_mosi.value) == 1
    assert await read(dut, 0x8) == 0, "must not be busy after reset"
    assert await read(dut, 0x0) == 0xFF, "receive register resets to 0xFF"
    await write(dut, 0x4, 0)
    assert int(dut.spi_cs_n.value) == 0 and await read(dut, 0x4) == 0
    await write(dut, 0x4, 1)
    assert int(dut.spi_cs_n.value) == 1 and await read(dut, 0x4) == 1


@cocotb.test()
async def test_full_duplex_byte(dut):
    """One transfer clocks 8 bits out MSB-first and captures the slave's byte"""
    slave = await setup(dut)
    await write(dut, 0x4, 0)
    for tx, reply in ((0xA5, 0x3C), (0x01, 0x80), (0xFE, 0x7F)):
        slave.load(reply)
        await write(dut, 0x0, tx)
        assert await read(dut, 0x8) == 1, "busy must be set during a transfer"
        await wait_idle(dut)
        assert len(slave.rx_bits) == 8, f"expected 8 SCLK pulses, saw {len(slave.rx_bits)}"
        assert slave.received() == tx, f"MOSI sent 0x{slave.received():02X}, expected 0x{tx:02X}"
        assert await read(dut, 0x0) == reply, f"received byte wrong for reply 0x{reply:02X}"
        assert int(dut.spi_sclk.value) == 0, "SCLK must idle low (mode 0)"
        assert int(dut.spi_mosi.value) == 1, "MOSI must idle high between transfers"


@cocotb.test()
async def test_sclk_divider(dut):
    """SCLK period is 2 * CLK_DIV_HALF system clocks (~391 kHz at 18 MHz)"""
    slave = await setup(dut)
    slave.load(0x00)
    await write(dut, 0x0, 0x55)
    await wait_idle(dut)
    periods = {round(b - a) for a, b in zip(slave.rise_times, slave.rise_times[1:], strict=False)}
    assert periods == {2 * CLK_DIV_HALF * 10}, f"SCLK periods (ns): {periods}"


@cocotb.test()
async def test_writes_ignored_while_busy(dut):
    """While busy, neither a new byte nor a chip-select change is accepted"""
    slave = await setup(dut)
    await write(dut, 0x4, 0)
    slave.load(0x96)
    await write(dut, 0x0, 0xC3)
    await ClockCycles(dut.clk, 100)
    await write(dut, 0x4, 1)  # dropped: busy
    await write(dut, 0x0, 0x00)  # dropped: busy
    assert int(dut.spi_cs_n.value) == 0, "CS write during a transfer must be ignored"
    await wait_idle(dut)
    assert slave.received() == 0xC3, "a write during the transfer corrupted the byte in flight"
    assert len(slave.rx_bits) == 8, "a write during the transfer started an extra transfer"
    assert await read(dut, 0x0) == 0x96
    await write(dut, 0x4, 1)
    assert int(dut.spi_cs_n.value) == 1, "CS write after the transfer must take effect"
