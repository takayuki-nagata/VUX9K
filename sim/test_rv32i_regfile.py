import cocotb
from cocotb.triggers import ClockCycles, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_rv32i_regfile(dut):
    """Test 32x32-bit register file with dual-write ports and x0 wired to 0"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    dut.rst.value = 0
    dut.we.value = 0
    dut.rd_addr.value = 0
    dut.wr_data.value = 0
    dut.we2.value = 0
    dut.rd2_addr.value = 0
    dut.wr2_data.value = 0
    dut.rs1_addr.value = 0
    dut.rs2_addr.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 1)

    # 2. Write to x0 on both ports (should be discarded)
    dut.we.value = 1
    dut.rd_addr.value = 0
    dut.wr_data.value = 0xDEADBEEF
    dut.we2.value = 1
    dut.rd2_addr.value = 0
    dut.wr2_data.value = 0xCAFEBABE
    await ClockCycles(dut.clk, 1)
    dut.we.value = 0
    dut.we2.value = 0
    dut.rs1_addr.value = 0
    await Timer(1, unit="ns")
    assert int(dut.rs1_data.value) == 0, "x0 must always be 0!"

    # 3. Simultaneous write: Port 1 writes x1 = 0x1234_5678, Port 2 writes x2 = 0x8765_4321
    dut.we.value = 1
    dut.rd_addr.value = 1
    dut.wr_data.value = 0x1234_5678
    dut.we2.value = 1
    dut.rd2_addr.value = 2
    dut.wr2_data.value = 0x8765_4321
    await ClockCycles(dut.clk, 1)

    # 4. Simultaneous read back: rs1 = x1, rs2 = x2
    dut.we.value = 0
    dut.we2.value = 0
    dut.rs1_addr.value = 1
    dut.rs2_addr.value = 2
    await Timer(1, unit="ns")
    assert int(dut.rs1_data.value) == 0x1234_5678, f"Expected 0x12345678, got {hex(int(dut.rs1_data.value))}"
    assert int(dut.rs2_data.value) == 0x8765_4321, f"Expected 0x87654321, got {hex(int(dut.rs2_data.value))}"

    dut._log.info("Dual-write register file verified successfully [PASS]")
