# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

CSR_MSTATUS = 0x300
CSR_MISA = 0x301
CSR_MIE = 0x304
CSR_MTVEC = 0x305
CSR_MSCRATCH = 0x340
CSR_MEPC = 0x341
CSR_MCAUSE = 0x342
CSR_MTVAL = 0x343
CSR_MIP = 0x344

FUNCT3_PRIV = 0b000
FUNCT3_CSRRW = 0b001
FUNCT3_CSRRS = 0b010
FUNCT3_CSRRC = 0b011
FUNCT3_CSRRWI = 0b101
FUNCT3_CSRRSI = 0b110
FUNCT3_CSRRCI = 0b111


@cocotb.test()
async def test_rv32i_csrs_basic(dut):
    """Test RISC-V RV32I Machine-Mode CSRs and Trap Unit"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # 1. Reset (active-low)
    dut.soft_rst.value = 0
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
    dut.csr_wdata.value = 0x0000_0008  # MIE = 1
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


async def csr_reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig in (
        "soft_rst",
        "rst",
        "csr_addr",
        "csr_wdata",
        "csr_op",
        "trap_entry",
        "trap_cause",
        "trap_pc",
        "trap_val",
        "trap_return",
        "timer_irq_in",
        "ext_irq_in",
        "sw_irq_in",
    ):
        getattr(dut, sig).value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 1)


async def csr_write(dut, addr, value):
    dut.csr_addr.value = addr
    dut.csr_wdata.value = value
    dut.csr_op.value = FUNCT3_CSRRW
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    await Timer(1, unit="ns")
    return int(dut.csr_rdata.value)


@cocotb.test()
async def test_rv32i_csrs_warl(dut):
    """M-mode-only WARL fields: mstatus keeps only MIE/MPIE with MPP reading as M,
    mie only MSIE/MTIE/MEIE, mtvec and mepc drop their low two bits"""
    await csr_reset(dut)
    assert await csr_write(dut, CSR_MSTATUS, 0xFFFF_FFFF) == 0x0000_1888
    assert await csr_write(dut, CSR_MSTATUS, 0) == 0x0000_1800, "MPP must stay M after clearing"
    assert await csr_write(dut, CSR_MIE, 0xFFFF_FFFF) == 0x0000_0888
    assert await csr_write(dut, CSR_MTVEC, 0x0000_0203) == 0x0000_0200, "mtvec is direct-mode only"
    assert await csr_write(dut, CSR_MEPC, 0x0000_0307) == 0x0000_0304


@cocotb.test()
async def test_rv32i_csrs_irq_cause_priority(dut):
    """irq_cause picks the highest-priority enabled pending interrupt: MEI > MSI > MTI"""
    await csr_reset(dut)
    await csr_write(dut, CSR_MIE, 0x888)
    await csr_write(dut, CSR_MSTATUS, 0x8)
    cases = [
        ((1, 0, 0), 0x8000_0007),
        ((1, 0, 1), 0x8000_0003),
        ((1, 1, 1), 0x8000_000B),
        ((0, 1, 0), 0x8000_000B),
    ]
    for (timer, ext, sw), cause in cases:
        dut.timer_irq_in.value = timer
        dut.ext_irq_in.value = ext
        dut.sw_irq_in.value = sw
        await Timer(1, unit="ns")
        assert int(dut.irq_pending.value) == 1
        assert int(dut.irq_cause.value) == cause, f"timer={timer} ext={ext} sw={sw}"

    # An interrupt that is pending but not enabled in mie must not decide the cause
    await csr_write(dut, CSR_MIE, 0x800)  # MEIE only
    dut.timer_irq_in.value = 1
    dut.ext_irq_in.value = 1
    dut.sw_irq_in.value = 0
    await Timer(1, unit="ns")
    assert int(dut.irq_cause.value) == 0x8000_000B
