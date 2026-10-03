# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import random

import cocotb
import fcov
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from unit_models import rand32

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
    dut.csr_wr.value = 0
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
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x8000_0000, f"Expected MTVEC=0x80000000, got {hex(int(dut.csr_rdata.value))}"
    assert int(dut.mtvec_out.value) == 0x8000_0000, "mtvec_out mismatch"

    # 4. Test CSRRW (Write mscratch = 0x1234_5678)
    dut.csr_addr.value = CSR_MSCRATCH
    dut.csr_wdata.value = 0x1234_5678
    dut.csr_op.value = FUNCT3_CSRRW
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
    await Timer(1, unit="ns")
    assert int(dut.csr_rdata.value) == 0x1234_5678, "mscratch readback mismatch"

    # 5. Test CSRRS (Bit Set: mstatus MIE bit 3)
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008  # MIE = 1
    dut.csr_op.value = FUNCT3_CSRRS
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
    await Timer(1, unit="ns")
    assert (int(dut.csr_rdata.value) & 0x8) != 0, "MIE bit not set"

    # 6. Test CSRRC (Bit Clear: mstatus MIE bit 3)
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008
    dut.csr_op.value = FUNCT3_CSRRC
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
    await Timer(1, unit="ns")
    assert (int(dut.csr_rdata.value) & 0x8) == 0, "MIE bit not cleared"

    # 7. Test Interrupt Pending (irq_pending) with Timer Interrupt
    # Set mie.MTIE (bit 7)
    dut.csr_addr.value = CSR_MIE
    dut.csr_wdata.value = 0x0000_0080
    dut.csr_op.value = FUNCT3_CSRRW
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0

    # Timer IRQ high, but mstatus.MIE = 0 -> irq_pending should be 0
    dut.timer_irq_in.value = 1
    await Timer(1, unit="ns")
    assert int(dut.irq_pending.value) == 0, "irq_pending should be 0 when MIE=0"

    # Set mstatus.MIE = 1 -> irq_pending should become 1
    dut.csr_addr.value = CSR_MSTATUS
    dut.csr_wdata.value = 0x0000_0008
    dut.csr_op.value = FUNCT3_CSRRS
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
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
        "csr_wr",
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
    dut.csr_wr.value = 1
    await ClockCycles(dut.clk, 1)
    dut.csr_op.value = 0
    dut.csr_wr.value = 0
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


@cocotb.test()
async def test_rv32i_csrs_counters(dut):
    """mcycle counts clocks, minstret counts retire pulses, time reads mtime_in; the
    user-level cycle/time/instret CSRs are read-only views; mcycle/minstret are writable"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig in (
        "soft_rst",
        "csr_addr",
        "csr_wdata",
        "csr_op",
        "csr_wr",
        "trap_entry",
        "trap_cause",
        "trap_pc",
        "trap_val",
        "trap_return",
        "timer_irq_in",
        "ext_irq_in",
        "sw_irq_in",
        "retire",
    ):
        getattr(dut, sig).value = 0
    dut.mtime_in.value = 0x0123_4567_89AB_CDEF
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 1

    async def read(addr):
        dut.csr_addr.value = addr
        await Timer(1, unit="ns")
        return int(dut.csr_rdata.value)

    assert await read(0xC01) == 0x89AB_CDEF, "time must read mtime_in[31:0]"
    assert await read(0xC81) == 0x0123_4567, "timeh must read mtime_in[63:32]"

    c0 = await read(0xB00)
    await ClockCycles(dut.clk, 10)
    c1 = await read(0xB00)
    assert c1 - c0 == 10, f"mcycle advanced {c1 - c0} in 10 clocks"
    assert await read(0xC00) == c1, "cycle must read mcycle"

    i0 = await read(0xB02)
    dut.retire.value = 1
    await ClockCycles(dut.clk, 3)
    dut.retire.value = 0
    await ClockCycles(dut.clk, 5)
    i1 = await read(0xB02)
    assert i1 - i0 == 3, f"minstret advanced {i1 - i0} for 3 retire pulses"
    assert await read(0xC02) == i1, "instret must read minstret"

    # Writes: mcycle high/low, minstret low; the user-level views ignore writes
    for addr, value in ((0xB80, 0x0000_0005), (0xB00, 0xFFFF_FFF0), (0xB02, 0x0000_0100)):
        dut.csr_addr.value = addr
        dut.csr_wdata.value = value
        dut.csr_op.value = FUNCT3_CSRRW
        dut.csr_wr.value = 1
        await ClockCycles(dut.clk, 1)
        dut.csr_op.value = 0
        dut.csr_wr.value = 0
    assert await read(0xB80) == 5, "mcycleh write"
    assert await read(0xB02) == 0x100, "minstret write (no retire pulse since)"
    await ClockCycles(dut.clk, 20)  # mcycle low wraps from 0xFFFF_FFF0 into mcycleh
    assert await read(0xB80) == 6, "mcycle must carry from the low into the high word"


class CsrModel:
    """rv32i_csrs as README "RV32 CSRs" describes it: WARL masks, trap entry/MRET, counters."""

    WRITE_MASK = {
        CSR_MSTATUS: 0x0000_0088,
        CSR_MIE: 0x0000_0888,
        CSR_MTVEC: 0xFFFF_FFFC,
        CSR_MSCRATCH: 0xFFFF_FFFF,
        CSR_MEPC: 0xFFFF_FFFC,
        CSR_MCAUSE: 0xFFFF_FFFF,
        CSR_MTVAL: 0xFFFF_FFFF,
    }

    def __init__(self, mcycle, minstret):
        self.regs = dict.fromkeys(self.WRITE_MASK, 0)
        self.mcycle, self.minstret = mcycle, minstret

    def read(self, addr, irqs, mtime):
        timer, ext, sw = irqs
        counters = {
            0xB00: self.mcycle,
            0xB80: self.mcycle >> 32,
            0xB02: self.minstret,
            0xB82: self.minstret >> 32,
            0xC01: mtime,
            0xC81: mtime >> 32,
        }
        counters |= {a + 0x100: counters[a] for a in (0xB00, 0xB80, 0xB02, 0xB82)}  # cycle/instret views
        if addr == CSR_MSTATUS:
            return self.regs[addr] | 0x1800
        if addr == CSR_MISA:
            return 0x4000_0100
        if addr == CSR_MIP:
            return timer << 7 | ext << 11 | sw << 3
        if addr in self.regs:
            return self.regs[addr]
        return counters.get(addr, 0) & 0xFFFF_FFFF

    def clock(self, s, rdata):
        """One rising edge with the inputs of state s (rdata: the CSR's value before it)."""
        wr, addr = s["csr_wr"], s["csr_addr"]
        if not (wr and addr in (0xB00, 0xB80)):
            self.mcycle = (self.mcycle + 1) & (1 << 64) - 1
        if not (wr and addr in (0xB02, 0xB82)):
            self.minstret = (self.minstret + s["retire"]) & (1 << 64) - 1
        r = self.regs
        if s["trap_entry"]:
            r[CSR_MEPC], r[CSR_MCAUSE], r[CSR_MTVAL] = s["trap_pc"], s["trap_cause"], s["trap_val"]
            mie = r[CSR_MSTATUS] >> 3 & 1
            r[CSR_MSTATUS] = mie << 7  # MPIE <= MIE, MIE <= 0
        elif s["trap_return"]:
            r[CSR_MSTATUS] = 0x80 | (r[CSR_MSTATUS] >> 7 & 1) << 3  # MIE <= MPIE, MPIE <= 1
        elif wr:
            op, d = s["csr_op"], s["csr_wdata"]
            value = {1: d, 5: d, 2: rdata | d, 6: rdata | d, 3: rdata & ~d, 7: rdata & ~d}.get(op, rdata)
            value &= 0xFFFF_FFFF
            if addr in self.WRITE_MASK:
                r[addr] = value & self.WRITE_MASK[addr]
            elif addr in (0xB00, 0xB80):
                shift = 32 if addr == 0xB80 else 0
                self.mcycle = self.mcycle & ~(0xFFFF_FFFF << shift) | value << shift
            elif addr in (0xB02, 0xB82):
                shift = 32 if addr == 0xB82 else 0
                self.minstret = self.minstret & ~(0xFFFF_FFFF << shift) | value << shift


_WRITABLE = (CSR_MSTATUS, CSR_MIE, CSR_MTVEC, CSR_MSCRATCH, CSR_MEPC, CSR_MCAUSE, CSR_MTVAL, 0xB00, 0xB80, 0xB02, 0xB82)


def _irq(s, mstatus, mie):
    """Which interrupt is pending and enabled (MEI > MSI > MTI), masked by mstatus.MIE or not."""
    pending = mie & (s["timer_irq_in"] << 7 | s["ext_irq_in"] << 11 | s["sw_irq_in"] << 3)
    if not pending:
        return None
    cause = "mei" if pending & 0x800 else "msi" if pending & 0x8 else "mti"
    return cause if mstatus & 0x8 else "masked"


# A write of every op to every writable CSR (the counters' halves included), a write in
# the same cycle as a trap return, and each interrupt cause taken or masked
@fcov.point("csrs.write", [(a, op) for a in _WRITABLE for op in (1, 2, 3, 5, 6, 7)],
            xf=lambda s, mstatus, mie: (s["csr_addr"], s["csr_op"]) if s["csr_wr"] else None)  # fmt: skip
@fcov.point("csrs.write_with_mret", (True,), xf=lambda s, mstatus, mie: bool(s["csr_wr"] and s["trap_return"]))
@fcov.point("csrs.irq", ("mei", "msi", "mti", "masked"), xf=lambda s, mstatus, mie: _irq(s, mstatus, mie))
def sample(s, mstatus, mie):
    pass


@cocotb.test()
async def test_rv32i_csrs_random(dut):
    """Random CSR reads/writes (every op, listed and unlisted addresses), trap entries,
    MRETs, interrupt lines and retire pulses against CsrModel, every cycle; also checks
    irq_pending/irq_cause"""
    dut.retire.value = 0
    dut.mtime_in.value = 0
    await csr_reset(dut)
    addrs = list(CsrModel.WRITE_MASK) + [CSR_MISA, CSR_MIP, 0xB00, 0xB80, 0xB02, 0xB82]
    addrs += [0xC00, 0xC80, 0xC02, 0xC82, 0xC01, 0xC81, 0x310, 0xF14, 0x7A0, 0x000, 0xFFF]

    async def read(addr):
        dut.csr_addr.value = addr
        await Timer(1, unit="ns")
        return int(dut.csr_rdata.value)

    await FallingEdge(dut.clk)
    # Sync the model's counters with the DUT's (both halves read within one cycle)
    mcycle = await read(0xB00) | await read(0xB80) << 32
    minstret = await read(0xB02) | await read(0xB82) << 32
    model = CsrModel(mcycle, minstret)

    # The first cycles write every op to every writable CSR once, in random order
    writes = [(a, op) for a in _WRITABLE for op in (1, 2, 3, 5, 6, 7)]
    random.shuffle(writes)
    for i in range(3000):
        r = random.random()
        s = {
            "csr_addr": random.choice(addrs) if random.random() < 0.9 else random.getrandbits(12),
            "csr_wdata": rand32(),
            "csr_op": random.randrange(8),
            "csr_wr": int(r < 0.5),
            "trap_entry": int(0.5 <= r < 0.6),
            "trap_return": int(0.6 <= r < 0.7 or (r < 0.05)),  # sometimes alongside a write
            "trap_cause": rand32(),
            "trap_pc": rand32(),
            "trap_val": rand32(),
            "timer_irq_in": random.getrandbits(1),
            "ext_irq_in": random.getrandbits(1),
            "sw_irq_in": random.getrandbits(1),
            "retire": random.getrandbits(1),
            "mtime_in": random.getrandbits(64),
        }
        if i < len(writes):
            s |= {"csr_wr": 1, "trap_entry": 0, "trap_return": 0}
            s["csr_addr"], s["csr_op"] = writes[i]
        sample(s, model.regs[CSR_MSTATUS], model.regs[CSR_MIE])
        for name, value in s.items():
            getattr(dut, name).value = value
        await Timer(1, unit="ns")
        irqs = (s["timer_irq_in"], s["ext_irq_in"], s["sw_irq_in"])
        rdata = model.read(s["csr_addr"], irqs, s["mtime_in"])
        assert int(dut.csr_rdata.value) == rdata, f"read 0x{s['csr_addr']:03x}: {s}"

        pending = model.read(CSR_MIE, irqs, 0) & model.read(CSR_MIP, irqs, 0)
        want_pending = int(bool(model.regs[CSR_MSTATUS] & 0x8) and bool(pending))
        assert int(dut.irq_pending.value) == want_pending, f"irq_pending: {s}"
        if pending:
            cause = 0x8000_000B if pending & 0x800 else 0x8000_0003 if pending & 0x8 else 0x8000_0007
            assert int(dut.irq_cause.value) == cause, f"irq_cause: {s}"

        await FallingEdge(dut.clk)
        model.clock(s, rdata)
    fcov.export()
