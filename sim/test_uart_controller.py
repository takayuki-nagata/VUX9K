import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

async def loopback_wire(dut):
    """Continuously loop back txd to rxd"""
    while True:
        dut.rxd.value = dut.txd.value
        await FallingEdge(dut.clk)

@cocotb.test()
async def test_uart_controller(dut):
    """Test UART Controller full duplex TX -> RX FIFO loopback communication"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    cocotb.start_soon(loopback_wire(dut))

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.re.value = 0
    dut.wdata.value = 0
    dut.rxd.value = 1

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    
    # Wait until reset state machine settles to IDLE
    for _ in range(500):
        await FallingEdge(dut.clk)

    assert int(dut.empty.value) == 1, "RX FIFO should initially be empty"
    assert int(dut.full.value) == 0, "TX FIFO should initially not be full"

    # 2. Write 3 bytes to TX FIFO: 0x48 ('H'), 0x69 ('i'), 0x21 ('!')
    test_bytes = [0x48, 0x69, 0x21]
    for b in test_bytes:
        dut.wdata.value = b
        dut.we.value = 1
        await FallingEdge(dut.clk)
    dut.we.value = 0

    # 3. Read back each byte from RX FIFO after transmission over loopback
    received_bytes = []
    for expected_byte in test_bytes:
        # Wait until byte arrives in RX FIFO (empty == 0)
        timeout = 0
        while int(dut.empty.value) == 1 and timeout < 20000:
            await FallingEdge(dut.clk)
            timeout += 1

        assert int(dut.empty.value) == 0, f"Timed out waiting for byte 0x{expected_byte:02X}"
        await Timer(1, unit="ns")
        rec_val = int(dut.rdata.value)
        received_bytes.append(rec_val)
        dut._log.info(f"Loopback received: 0x{rec_val:02X} ('{chr(rec_val)}')")

        # Pop from RX FIFO
        dut.re.value = 1
        await FallingEdge(dut.clk)
        dut.re.value = 0
        await FallingEdge(dut.clk)

    assert received_bytes == test_bytes, f"Data mismatch! Expected {test_bytes}, got {received_bytes}"
    assert int(dut.empty.value) == 1, "RX FIFO should be empty after reading all bytes"

    dut._log.info("UART Controller full loopback test passed 100% [PASS]")
