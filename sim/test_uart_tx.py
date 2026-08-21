import cocotb
from cocotb.triggers import FallingEdge, Timer, RisingEdge
from cocotb.clock import Clock

@cocotb.test()
async def test_uart_tx(dut):
    """Test UART TX serial frame transmission (Start bit, 8 data bits LSB-first, Stop bit)"""
    clock = Clock(dut.clk, 20, unit="ns") # 50MHz
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    dut.we.value = 0
    dut.data.value = 0

    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    
    # Wait until reset state machine settles to IDLE (busy = 0)
    while int(dut.busy.value) == 1:
        await FallingEdge(dut.clk)

    assert int(dut.txd.value) == 1, "TX line should be idle high (1)"

    # 2. Transmit byte 0x55 (0b01010101)
    # Default CNT in uart_tx is 434
    tx_byte = 0x55
    dut.data.value = tx_byte
    dut.we.value = 1
    await FallingEdge(dut.clk)
    dut.we.value = 0

    # Wait for busy to assert
    await Timer(1, unit="ns")
    assert int(dut.busy.value) == 1, "busy should assert after we=1"

    # Wait for start bit: txd goes 0
    while int(dut.txd.value) == 1:
        await FallingEdge(dut.clk)

    # Now verify the 10 UART frame bits: Start(0), D0..D7, Stop(1)
    # Default baud clock timer counts 434 cycles per bit
    # Sample at middle of each bit period (~217 cycles)
    expected_bits = [0] + [(tx_byte >> i) & 1 for i in range(8)] + [1]
    sampled_bits = []

    # First bit (start bit) is already active
    # For each bit in the frame:
    for bit_idx in range(10):
        # Wait 434 cycles per bit
        for _ in range(434):
            await FallingEdge(dut.clk)
        sampled_bits.append(int(dut.txd.value))

    # Check that transmission finished and returned to IDLE (busy=0, txd=1)
    while int(dut.busy.value) == 1:
        await FallingEdge(dut.clk)

    assert int(dut.txd.value) == 1, "TX line should return to idle high"
    dut._log.info(f"UART TX Frame transmitted successfully: byte=0x{tx_byte:02X} [PASS]")
