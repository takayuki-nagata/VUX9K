# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.triggers import ClockCycles, Timer
from cocotb.clock import Clock

CSR_MSTATUS  = 0x300
CSR_MISA     = 0x301
CSR_MIE      = 0x304
CSR_MTVEC    = 0x305
CSR_MSCRATCH = 0x340
CSR_MEPC     = 0x341
CSR_MCAUSE   = 0x342
CSR_MTVAL    = 0x343
CSR_MIP      = 0x344

FUNCT3_PRIV   = 0b000
FUNCT3_CSRRW  = 0b001
FUNCT3_CSRRS  = 0b010
FUNCT3_CSRRC  = 0b011
FUNCT3_CSRRWI = 0b101
FUNCT3_CSRRSI = 0b110
FUNCT3_CSRRCI = 0b111

@cocotb.test()
async def test_rv32i_csrs_basic(dut):
    """Test RISC-V RV32I Machine-Mode CSRs and Trap Unit"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    dut.rst.value = 0
    dut.csr_addr.value = 0
    dut.csr_wdata.value = 0
    dut.csr_op.value = 0
    dut.trap_entry.value = 0
    dut.trap_cause.value = 0
    dut.trap_pc.value = 0
    dut.trap_val.value = 0
    dut.trap_return.value = 0
    dut.timer_irq_in.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0

    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 1)

    # 2. Check Read of MISA
    dut.csr_addr.value = CSR_MISA
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x40000100, f"Expected MISA=0x40000100, got {hex(int(dut.csr_rdata.value))}"

    # 3. Test CSRRW (Write mtvec = 0x8000_0000)
    dut.csr_addr.value = CSR_MTVEC
    dut.csr_wdata.value = 0x8000_0000
    dut.csr_op.value = FUNCT3_CSRRW
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x8000_0000, f"Expected MTVEC=0x80000000, got {hex(int(dut.csr_rdata.value))}"
    assert int(dut.mtvec_out.value) == 0x8000_0000, "mtvec_out mismatch"

    # 4. Test CSRRW (Write mscratch = 0x1234_5678)
    dut.csr_addr.value = CSR_MSCRATCH
    dut.csr_wdata.value = 0x1234_5678
    dut.csr_op.value = FUNCT3_CSRRW
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x1234_5678, "mscratch readback mismatch"

    # 5. Test CSRRS (Bit Set: mstatus MIE bit 3)
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008 # MIE = 1
    dut.csr_op.value = FUNCT3_CSRRS
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    assert (int(dut.csr_rdata.value) & 0x8) != 0, "MIE bit not set"

    # 6. Test CSRRC (Bit Clear: mstatus MIE bit 3)
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008
    dut.csr_op.value = FUNCT3_CSRRC
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    assert (int(dut.csr_rdata.value) & 0x8) == 0, "MIE bit not cleared"

    # 7. Test Interrupt Pending (irq_pending) with Timer Interrupt
    # Set mie.MTIE (bit 7)
    dut.csr_addr.value = CSR_MIE
    dut.csr_wdata.value = 0x0000_0080
    dut.csr_op.value = FUNCT3_CSRRW
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0

    # Timer IRQ high, but mstatus.MIE = 0 -> irq_pending should be 0
    dut.timer_irq_in.value = 1
    await Timer(1, unit="ns")
    assert int(dut.irq_pending.value) == 0, "irq_pending should be 0 when MIE=0"

    # Set mstatus.MIE = 1 -> irq_pending should become 1
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008
    dut.csr_op.value = FUNCT3_CSRRS
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    assert int(dut.irq_pending.value) == 1, "irq_pending should be 1 when MIE=1 and MTIE=1 and timer_irq_in=1"

    # Clear timer_irq_in -> irq_pending should become 0
    dut.timer_irq_in.value = 0
    await Timer(1, unit="ns")
    assert int(dut.irq_pending.value) == 0, "irq_pending should be 0 when timer_irq_in=0"

    # 8. Test Trap Entry (e.g. ECALL from M-mode: cause=11, pc=0x0000_0400)
    dut.trap_entry.value = 1
    dut.trap_cause.value = 11
    dut.trap_pc.value = 0x0000_0400
    dut.trap_val.value = 0
    await ClockCycles(dut.clk, 1)
    dut.trap_entry.value = 0
    await Timer(1, unit="ns")

    # Check mepc = 0x0400, mcause = 11, mstatus.MIE = 0, mstatus.MPIE = 1 (since MIE was 1)
    dut.csr_addr.value = CSR_MEPC
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x0000_0400, f"Expected MEPC=0x400, got {hex(int(dut.csr_rdata.value))}"
    assert int(dut.mepc_out.value) == 0x0000_0400, "mepc_out mismatch"

    dut.csr_addr.value = CSR_MCAUSE
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 11, f"Expected MCAUSE=11, got {int(dut.csr_rdata.value)}"

    dut.csr_addr.value = CSR_MSTATUS
    await Timer(1, unit="ns")
    status = int(dut.csr_rdata.value)
    assert (status & 0x08) == 0, "MIE should be 0 after trap entry"
    assert (status & 0x80) != 0, "MPIE should be 1 after trap entry"

    # 9. Test Trap Return (MRET)
    dut.trap_return.value = 1
    await ClockCycles(dut.clk, 1)
    dut.trap_return.value = 0
    await Timer(1, unit="ns")

    # Check mstatus: MIE should be restored to MPIE (1), MPIE should be 1
    dut.csr_addr.value = CSR_MSTATUS
    await Timer(1, unit="ns")
    status = int(dut.csr_rdata.value)
    assert (status & 0x08) != 0, "MIE should be restored to 1 on MRET"
    assert (status & 0x80) != 0, "MPIE should be 1 on MRET"

    dut._log.info("RISC-V Machine-Mode CSRs & Trap Unit verified successfully [PASS]")
