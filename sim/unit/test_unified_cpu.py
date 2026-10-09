# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from rv32_asm import Asm
from test_unified_cpu_traps import run_program


@cocotb.test()
async def test_unified_cpu_hack_and_riscv(dut):
    """Test standalone unified_cpu dual-ISA execution (Hack 16-bit + RISC-V 32-bit) in Multi-cycle FSM"""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    # =========================================================================
    # TEST 1: Hack 16-bit Mode Execution
    # =========================================================================
    await FallingEdge(dut.clk)
    dut.soft_rst.value = 0
    dut.rst.value = 0
    dut.data_in.value = 0
    dut.timer_irq_in.value = 0
    dut.mtime_in.value = 0
    dut.boot_mode_valid.value = 0
    dut.boot_mode.value = 0
    dut.ext_irq_in.value = 0
    dut.sw_irq_in.value = 0
    dut.instr_ready.value = 1
    dut.data_ready.value = 1
    dut.instr_in.value = (0x000F << 16) | 0x000F  # Hack @15 (Load 15 into A/x1)

    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Cycle 1: Fetch -> Cycle 2: Execute (@15) -> Next PC becomes 2
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 0, "Hack mode auto-detection failed!"
    assert int(dut.pc_out.value) == 2, f"Expected PC=2, got {int(dut.pc_out.value)}"

    # PC=2: Hack C-instruction D=A (0xEC10 -> "1110110000010000")
    dut.instr_in.value = (0xEC10 << 16) | 0xEC10
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: Hack C-instruction D=D+1 (0xE7D0 -> "1110011111010000")
    dut.instr_in.value = (0xE7D0 << 16) | 0xE7D0
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 6, f"Expected PC=6, got {int(dut.pc_out.value)}"

    # PC=6: Hack C-instruction M=D (0xE308 -> "1110001100001000")
    dut.instr_in.value = (0xE308 << 16) | 0xE308
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "Hack memory write assertion failed"
    assert int(dut.data_addr.value) == 15, f"Expected addr=15, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 16, f"Expected data=16, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)

    dut._log.info("[PASS] Unified CPU: Hack 16-bit program execution verified!")

    # =========================================================================
    # TEST 2: RISC-V 32-bit Mode Execution
    # =========================================================================
    dut.rst.value = 0
    dut.instr_in.value = 0x02A00513  # ADDI x10, x0, 42
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 1

    # Cycle 1: Fetch -> Cycle 2: Execute -> PC becomes 4
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.active_mode.value) == 1, "RISC-V mode auto-detection failed!"
    assert int(dut.pc_out.value) == 4, f"Expected PC=4, got {int(dut.pc_out.value)}"

    # PC=4: ADDI x11, x10, 8 (42 + 8 = 50)
    dut.instr_in.value = 0x00850593
    await ClockCycles(dut.clk, 2)
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 8, f"Expected PC=8, got {int(dut.pc_out.value)}"

    # PC=8: SW x11, 100(x0) (Store 50 to address 100)
    dut.instr_in.value = 0x06B02223
    await ClockCycles(dut.clk, 2)  # FETCH -> EXECUTE -> MEM_WAIT
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "SW memory write assertion failed"
    assert int(dut.data_addr.value) == 100, f"Expected addr=100, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 50, f"Expected data=50, got {int(dut.data_out.value)}"
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> FETCH

    # PC=12: LW x12, 208(x0) (Load from address 208)
    # Binary: imm[11:0]=0x0D0, rs1=0, funct3=010, rd=12, opcode=0x03 -> 0x0D002603
    dut.instr_in.value = 0x0D002603
    await ClockCycles(dut.clk, 1)  # FETCH -> EXECUTE
    await Timer(1, unit="ns")
    assert int(dut.pc_out.value) == 16, f"Expected next PC=16, got {int(dut.pc_out.value)}"
    assert int(dut.data_addr.value) == 208, f"Expected load addr=208 during EXECUTE, got {int(dut.data_addr.value)}"

    # Provide data_in for the memory read
    dut.data_in.value = 0xCAFEBABE
    await ClockCycles(dut.clk, 1)  # EXECUTE -> MEM_WAIT
    await Timer(1, unit="ns")

    # In MEM_WAIT during a LOAD, mem_write must be 0 (read phase)
    assert int(dut.mem_write.value) == 0, f"Expected mem_write=0 on LOAD, got {int(dut.mem_write.value)}"
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> FETCH

    # PC=16: SW x12, 300(x0) (Store loaded value to verify x12 was written with data_in)
    # imm=300: imm[11:5]=0001001, rs2=12, rs1=0, funct3=010, imm[4:0]=01100, opcode=0100011 -> 0x12c02623
    dut.instr_in.value = 0x12C02623
    await ClockCycles(dut.clk, 2)  # FETCH -> EXECUTE -> MEM_WAIT
    await Timer(1, unit="ns")
    assert int(dut.mem_write.value) == 1, "SW memory write assertion failed"
    assert int(dut.data_addr.value) == 300, f"Expected addr=300, got {int(dut.data_addr.value)}"
    assert int(dut.data_out.value) == 0xCAFEBABE, f"Expected loaded data=0xCAFEBABE, got {hex(int(dut.data_out.value))}"
    await ClockCycles(dut.clk, 1)  # MEM_WAIT -> FETCH

    dut._log.info("[PASS] Unified CPU: RISC-V 32-bit program execution verified!")


@cocotb.test()
async def test_full_width_store_addresses(dut):
    """Stores put out all 32 address bits (each one set alone, and all of them), with the
    byte enables and replicated data of SB at every byte and SH at both halves"""
    a = Asm()
    want = []
    for addr in [1 << b for b in range(2, 32)] + [0xFFFF_FFFC]:
        a.li("t0", addr)
        a.li("t1", addr ^ 0x5A5A_5A5A)
        a.sw("t1", 0, "t0")
        want.append((addr, addr ^ 0x5A5A_5A5A, 0xF))
    a.li("t0", 0x2000_0100)
    a.li("t1", 0x1234_56A5)
    for k in range(4):
        a.sb("t1", k, "t0")
        want.append((0x2000_0100 + k, 0xA5A5_A5A5, 1 << k))
    for k in (0, 2):
        a.sh("t1", k, "t0")
        want.append((0x2000_0100 + k, 0x56A5_56A5, 3 << k))
    a.label("done")
    a.j("done")
    stores: list[tuple[int, int, int]] = []
    pcs, _ = await run_program(dut, a.assemble(), max_cycles=8 * len(want) + 400, stores=stores)
    assert a.labels["done"] in pcs, "never reached the end of the program"
    got = stores
    assert got == want, (
        "stores (address, data, byte enables): "
        + ", ".join(
            f"#{i} got {tuple(hex(v) for v in g)} want {tuple(hex(v) for v in w)}"
            for i, (g, w) in enumerate(zip(got, want, strict=False))
            if g != w
        )
        + f" ({len(got)} stores, {len(want)} expected)"
    )


@cocotb.test()
async def test_high_pc_continues_after_load_and_store(dut):
    """Code far up the address space: after a load or a store (MEM_WAIT), execution goes
    on at pc + 4 with every PC bit kept"""
    bases = (0xAAAA_A000, 0x5555_4000, 0xFFFF_F000)
    mem = {}
    a = Asm()
    a.li("t0", bases[0])
    a.jalr("zero", "t0")
    a.label("done")
    a.j("done")
    mem.update({i * 4: w for i, w in enumerate(a.assemble())})
    done = a.labels["done"]
    for i, base in enumerate(bases):
        b = Asm(origin=base)
        b.lw("t1", 0, "zero")
        b.label("after_load")
        b.sw("t1", 0, "zero")
        b.label("after_store")
        b.li("t0", bases[i + 1] if i + 1 < len(bases) else done)
        b.jalr("zero", "t0")
        mem.update({base + 4 * j: w for j, w in enumerate(b.assemble())})
    pcs, _ = await run_program(dut, mem, max_cycles=300)
    for base in bases:
        assert base + 4 in pcs and base + 8 in pcs, f"no pc + 4 after the load/store at 0x{base:08x}"
    assert done in pcs, f"never got back; last PCs {[hex(p) for p in pcs[-4:]]}"


# The RV32I opcodes the mode detector accepts as a first instruction (with bits [1:0] = 11)
RV_FIRST_OPCODES = (0x33, 0x13, 0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73)


@cocotb.test()
async def test_first_instruction_selects_mode(dut):
    """With no boot mode given, the first instruction word picks the ISA: each RV32I
    opcode above selects RISC-V; a word one opcode bit away from one of them that isn't
    an RV32I opcode itself (bit 15 clear, non-zero) is a Hack A-instruction"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for sig in (
        "soft_rst",
        "data_in",
        "timer_irq_in",
        "ext_irq_in",
        "sw_irq_in",
        "mtime_in",
        "boot_mode_valid",
        "boot_mode",
    ):
        getattr(dut, sig).value = 0
    dut.instr_ready.value = 1
    dut.data_ready.value = 1
    others = sorted({op ^ (1 << b) for op in RV_FIRST_OPCODES for b in range(2, 7)} - set(RV_FIRST_OPCODES))
    cases = [(op, 1) for op in RV_FIRST_OPCODES] + [(op, 0) for op in others]
    for word, want in cases:
        dut.rst.value = 0
        dut.instr_in.value = word
        await ClockCycles(dut.clk, 2)
        await FallingEdge(dut.clk)
        dut.rst.value = 1
        await ClockCycles(dut.clk, 3)
        await FallingEdge(dut.clk)
        got = int(dut.active_mode.value)
        assert got == want, f"first word 0x{word:08x}: active_mode {got}, want {want} ({'RV32' if want else 'Hack'})"


async def count_cycles_high(dut, signal, counts):
    """counts[0] += 1 for every cycle (sampled at the falling edge) with `signal` high after reset"""
    while True:
        await FallingEdge(dut.clk)
        if str(dut.rst.value) == "1" and str(getattr(dut, signal).value) == "1":
            counts[0] += 1


@cocotb.test()
async def test_mem_read_req_only_for_loads(dut):
    """mem_read_req (a read with side effects, e.g. popping the UART RX FIFO) is asserted
    for one cycle per load, and never for stores, ALU or CSR instructions or branches"""
    loads = 3
    a = Asm()
    a.li("t0", 0x100)
    for k in range(loads):
        a.lw("t1", 4 * k, "t0")
        a.sw("t1", 4 * k + 64, "t0")
        a.addi("t2", "t1", 1)
        a.csrr("t3", "mscratch")
        a.beq("zero", "t0", "done")  # not taken
    a.label("done")
    a.j("done")
    counts = [0]
    cocotb.start_soon(count_cycles_high(dut, "mem_read_req", counts))
    pcs, _ = await run_program(dut, a.assemble(), max_cycles=300)
    assert a.labels["done"] in pcs, "never reached the end of the program"
    assert counts[0] == loads, f"mem_read_req was high for {counts[0]} cycles, expected {loads} (one per load)"
