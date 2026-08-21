import pytest
from emulator import SocEmulator

def test_emulator_csr_operations():
    emu = SocEmulator()

    # mtvec write and read
    emu.write_csr(0x305, 0x00008000)
    assert emu.read_csr(0x305) == 0x00008000

    # mstatus write and read (MPP=3 forced, MIE=bit3 set)
    emu.write_csr(0x300, 0x00000008) # MIE=1
    assert (emu.read_csr(0x300) & 0x08) != 0

    # mie write and read (MTIE=bit7)
    emu.write_csr(0x304, 0x00000080) # MTIE=1
    assert (emu.read_csr(0x304) & 0x80) != 0

def test_emulator_trap_ecall_and_mret():
    emu = SocEmulator()
    emu.write_csr(0x305, 0x00001000) # mtvec = 0x1000
    emu.write_csr(0x300, 0x00000008) # MIE = 1

    # Place ECALL instruction (0x00000073) at PC = 0x0
    emu.i_ram[0] = 0x00000073
    emu.pc = 0x0
    emu.step()

    # Verify trap state
    assert emu.pc == 0x00001000
    assert emu.mepc == 0x00000000
    assert emu.mcause == 11
    assert (emu.mstatus & 0x08) == 0 # MIE cleared
    assert (emu.mstatus & 0x80) != 0 # MPIE = 1

    # Place MRET instruction (0x30200073) at PC = 0x1000
    emu.i_ram[0x1000 >> 2] = 0x30200073
    emu.step()

    # Verify return from trap
    assert emu.pc == 0x00000000
    assert (emu.mstatus & 0x08) != 0 # MIE restored to 1

def test_emulator_timer_interrupt():
    emu = SocEmulator()
    emu.write_csr(0x305, 0x00002000) # mtvec = 0x2000
    emu.write_csr(0x300, 0x00000008) # MIE = 1
    emu.write_csr(0x304, 0x00000080) # MTIE = 1

    emu.mtime = 100
    emu.mtimecmp = 100

    # Place NOP instruction (0x00000013) at PC = 0x0100
    emu.i_ram[0x0100 >> 2] = 0x00000013
    emu.pc = 0x0100

    emu.step()

    # Verify Timer Interrupt Trap
    assert emu.pc == 0x00002000
    assert emu.mepc == 0x00000100
    assert emu.mcause == 0x80000007
    assert (emu.mstatus & 0x08) == 0 # MIE cleared

def test_emulator_byte_and_halfword_stores():
    emu = SocEmulator()
    # SB: Store 0xAB at D-RAM 0x20000001
    emu.regs[1] = 0x20000000
    emu.regs[2] = 0xAB
    # SB x2, 1(x1) -> funct3=0, opcode=0x23, imm=1
    emu.i_ram[0] = 0x002080a3 # sb x2, 1(x1)
    emu.pc = 0x0
    emu.step()

    word = emu.read_mem(0x20000000)
    assert (word & 0x0000FF00) == 0x0000AB00

    # SH: Store 0x1234 at D-RAM 0x20000002
    emu.regs[2] = 0x1234
    emu.i_ram[1] = 0x00209123 # sh x2, 2(x1)
    emu.step()

    word = emu.read_mem(0x20000000)
    assert (word & 0xFFFF0000) == 0x12340000
    assert (word & 0x0000FF00) == 0x0000AB00
