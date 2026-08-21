import cocotb
from cocotb.triggers import FallingEdge, Timer
from cocotb.clock import Clock

@cocotb.test()
async def test_clk_timer(dut):
    """Test clk_timer periodic alarm pulse generation"""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await FallingEdge(dut.clk)

    # 2. Count alarm pulses over cycles
    # Default CNT in clk_timer is 434 (Tang Nano 9K 50MHz / 115200 baud)
    # Let's count for 434 * 3 cycles -> should get exactly 3 pulses
    alm_count = 0
    total_cycles = 434 * 3
    for _ in range(total_cycles):
        await FallingEdge(dut.clk)
        if int(dut.alm.value) == 1:
            alm_count += 1

    assert alm_count == 3, f"Expected 3 alarm pulses, got {alm_count}"
    dut._log.info(f"clk_timer verified successfully: received {alm_count} pulses in {total_cycles} cycles [PASS]")
