# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
sdcard_spi (SPI master for the MicroSD card) unit tests.

Register map (addr[3:0]): 0x0 write = start an 8-bit transfer, read = last received
byte; 0x4 chip select (bit 0 = cs_n); 0x8 busy (ro); 0xC SCLK half-period in system
clocks (resets to CLK_DIV_HALF = 23, writes below CLK_DIV_MIN = 3 set 3). SPI mode 0:
SCLK idles low, MISO is sampled on the rising edge (through a 2-flop synchronizer),
MOSI shifts on the falling edge, MSB first. Writes are only accepted while not busy --
including chip-select and divider writes.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer

CLK_DIV_HALF = 23  # soc_pkg::SD_CLK_DIV_HALF at 18 MHz
CLK_DIV_MIN = 3  # soc_pkg::SD_CLK_DIV_MIN
CLK_NS = 10
BYTE_CYCLES = 8 * 2 * 255  # the longest transfer, at the largest divider


class SpiSlave:
    """Mode-0 slave: captures MOSI on SCLK rising edges, shifts `reply` out MSB first."""

    def __init__(self, dut):
        self.dut = dut
        self.rx_bits = []
        self.rise_times = []
        self.reply = 0xFF
        self.delay_ns = 0  # MISO output delay after the falling edge (a card's tODLY)

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
            if self.delay_ns:
                await Timer(self.delay_ns, unit="ns")
            bit = len(self.rx_bits)
            if bit < 8:
                self.dut.spi_miso.value = (self.reply >> (7 - bit)) & 1

    def received(self):
        value = 0
        for b in self.rx_bits:
            value = (value << 1) | b
        return value


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
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


async def sclk_periods(dut, slave):
    """SCLK periods (ns) of one transfer."""
    slave.load(0x00)
    await write(dut, 0x0, 0x55)
    await wait_idle(dut)
    return {round(b - a) for a, b in zip(slave.rise_times, slave.rise_times[1:], strict=False)}


@cocotb.test()
async def test_sclk_divider(dut):
    """SCLK period is 2 * the divider register; it resets to CLK_DIV_HALF (~391 kHz at 18 MHz)"""
    slave = await setup(dut)
    assert await read(dut, 0xC) == CLK_DIV_HALF
    assert await sclk_periods(dut, slave) == {2 * CLK_DIV_HALF * CLK_NS}
    for div in (8, CLK_DIV_MIN, 255):
        await write(dut, 0xC, div)
        assert await read(dut, 0xC) == div
        assert await sclk_periods(dut, slave) == {2 * div * CLK_NS}, f"divider {div}"


@cocotb.test()
async def test_divider_clamped(dut):
    """Divider writes below CLK_DIV_MIN set CLK_DIV_MIN; only bits 7:0 are kept"""
    slave = await setup(dut)
    for div in (0, 1, 2):
        await write(dut, 0xC, div)
        assert await read(dut, 0xC) == CLK_DIV_MIN, f"wrote {div}"
    await write(dut, 0xC, 0x104)
    assert await read(dut, 0xC) == 4
    assert await sclk_periods(dut, slave) == {2 * 4 * CLK_NS}


@cocotb.test()
async def test_fastest_divider_reads_delayed_miso(dut):
    """At CLK_DIV_MIN, MISO driven up to almost a clock after the falling edge is read right"""
    slave = await setup(dut)
    await write(dut, 0xC, CLK_DIV_MIN)
    await write(dut, 0x4, 0)
    slave.delay_ns = CLK_NS - 1
    for tx, reply in ((0xA5, 0x3C), (0x01, 0x80), (0xFE, 0x7F), (0x00, 0xAA), (0xFF, 0x55)):
        slave.load(reply)
        await write(dut, 0x0, tx)
        await wait_idle(dut)
        assert slave.received() == tx, f"MOSI sent 0x{slave.received():02X}, expected 0x{tx:02X}"
        assert await read(dut, 0x0) == reply, f"received byte wrong for reply 0x{reply:02X}"


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
    await write(dut, 0xC, 5)  # dropped: busy
    assert int(dut.spi_cs_n.value) == 0, "CS write during a transfer must be ignored"
    await wait_idle(dut)
    assert slave.received() == 0xC3, "a write during the transfer corrupted the byte in flight"
    assert len(slave.rx_bits) == 8, "a write during the transfer started an extra transfer"
    assert await read(dut, 0xC) == CLK_DIV_HALF, "divider write during a transfer must be ignored"
    assert {round(b - a) for a, b in zip(slave.rise_times, slave.rise_times[1:], strict=False)} == {
        2 * CLK_DIV_HALF * CLK_NS
    }
    assert await read(dut, 0x0) == 0x96
    await write(dut, 0x4, 1)
    assert int(dut.spi_cs_n.value) == 1, "CS write after the transfer must take effect"
