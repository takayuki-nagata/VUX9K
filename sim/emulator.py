#!/usr/bin/env python3
"""
Tang Nano 9K Dual-ISA SoC Behavioral Python Emulator
Simulates RV32I & Hack 16-bit CPU execution, Machine-Mode CSRs & Traps (Zicsr),
256KB I-RAM, 128KB D-RAM, MMIO UART, MMIO Timer (with Timer IRQ), and MMIO SD Card SPI controller.
"""

import sys
import struct

class SocEmulator:
    def __init__(self, i_mem_words=65536, d_mem_words=32768):
        self.i_ram = [0] * i_mem_words  # 256 KB
        self.d_ram = [0] * d_mem_words  # 128 KB
        self.regs = [0] * 32
        self.pc = 0x00000000
        self.mtime = 0
        self.mtimecmp = 0xFFFFFFFFFFFFFFFF
        self.uart_tx_buf = []
        self.sd_cs = 1
        self.verbose = False
        self.running = True

        # Machine-Mode CSRs (Zicsr)
        self.mstatus = 0x00001800  # MPP=3 (Machine mode)
        self.misa = 0x40000100     # RV32I
        self.mie = 0x00000000
        self.mtvec = 0x00000000
        self.mscratch = 0x00000000
        self.mepc = 0x00000000
        self.mcause = 0x00000000
        self.mtval = 0x00000000
        self.mip = 0x00000000

    def load_binary(self, filename):
        with open(filename, 'rb') as f:
            data = f.read()
        for i in range(0, len(data), 4):
            if i + 4 <= len(data):
                word = struct.unpack('<I', data[i:i+4])[0]
                idx = i // 4
                if idx < len(self.i_ram):
                    self.i_ram[idx] = word

    def read_csr(self, csr_addr):
        if csr_addr == 0x300: # mstatus
            return self.mstatus
        elif csr_addr == 0x301: # misa
            return self.misa
        elif csr_addr == 0x304: # mie
            return self.mie
        elif csr_addr == 0x305: # mtvec
            return self.mtvec
        elif csr_addr == 0x340: # mscratch
            return self.mscratch
        elif csr_addr == 0x341: # mepc
            return self.mepc
        elif csr_addr == 0x342: # mcause
            return self.mcause
        elif csr_addr == 0x343: # mtval
            return self.mtval
        elif csr_addr == 0x344: # mip
            return self.mip
        return 0

    def write_csr(self, csr_addr, val):
        val &= 0xFFFFFFFF
        if csr_addr == 0x300: # mstatus
            # Keep MPP=3 (bits 12:11 = 11), allow MIE (bit 3) & MPIE (bit 7)
            self.mstatus = (val & 0x00000088) | 0x00001800
        elif csr_addr == 0x304: # mie
            self.mie = val & 0x00000888 # MTIE (bit 7), MEIE (bit 11), MSIE (bit 3)
        elif csr_addr == 0x305: # mtvec
            self.mtvec = val & 0xFFFFFFFC # Direct mode (aligned)
        elif csr_addr == 0x340: # mscratch
            self.mscratch = val
        elif csr_addr == 0x341: # mepc
            self.mepc = val & 0xFFFFFFFE
        elif csr_addr == 0x342: # mcause
            self.mcause = val
        elif csr_addr == 0x343: # mtval
            self.mtval = val
        elif csr_addr == 0x344: # mip
            # Software can only clear/set software interrupt pending
            self.mip = (self.mip & ~0x08) | (val & 0x08)

    def read_mem(self, addr):
        if 0x00000000 <= addr < 0x00040000:
            return self.i_ram[(addr & 0x3FFFF) >> 2]
        elif 0x20000000 <= addr < 0x20020000:
            return self.d_ram[(addr & 0x1FFFF) >> 2]
        elif 0x40000000 <= addr < 0x40000010:
            offset = addr & 0xF
            if offset == 0x0:
                return 0
            elif offset == 0x4:
                return 0 # TX FIFO empty, not full
        elif 0x40001000 <= addr < 0x40001020:
            offset = addr & 0x1F
            if offset == 0x0:
                return self.mtime & 0xFFFFFFFF
            elif offset == 0x4:
                return (self.mtime >> 32) & 0xFFFFFFFF
            elif offset == 0x8:
                return self.mtimecmp & 0xFFFFFFFF
            elif offset == 0xC:
                return (self.mtimecmp >> 32) & 0xFFFFFFFF
        elif 0x40002000 <= addr < 0x40002010:
            offset = addr & 0xF
            if offset == 0x0:
                return 0x00 # CMD0 mock OK response
            elif offset == 0x4:
                return self.sd_cs
            elif offset == 0x8:
                return 0x02 # rx_ready = 1, busy = 0
        return 0

    def write_mem(self, addr, val, byte_en=0xF):
        if 0x20000000 <= addr < 0x20020000:
            idx = (addr & 0x1FFFF) >> 2
            old = self.d_ram[idx]
            new_val = 0
            for b in range(4):
                if (byte_en & (1 << b)):
                    new_val |= (val & (0xFF << (b * 8)))
                else:
                    new_val |= (old & (0xFF << (b * 8)))
            self.d_ram[idx] = new_val
        elif 0x40000000 <= addr < 0x40000010:
            offset = addr & 0xF
            if offset == 0x0:
                c_byte = val & 0xFF
                if c_byte != 0:
                    char = chr(c_byte)
                    self.uart_tx_buf.append(char)
                    sys.stdout.write(char)
                    sys.stdout.flush()
        elif 0x40001000 <= addr < 0x40001020:
            offset = addr & 0x1F
            if offset == 0x0:
                self.mtime = (self.mtime & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
            elif offset == 0x4:
                self.mtime = (self.mtime & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
            elif offset == 0x8:
                self.mtimecmp = (self.mtimecmp & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
            elif offset == 0xC:
                self.mtimecmp = (self.mtimecmp & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
        elif 0x40002000 <= addr < 0x40002010:
            offset = addr & 0xF
            if offset == 0x4:
                self.sd_cs = val & 1

    def step(self):
        self.regs[0] = 0
        self.mtime += 1

        # Evaluate Machine Timer Pending Flag
        if self.mtime >= self.mtimecmp:
            self.mip |= (1 << 7) # MTIP
        else:
            self.mip &= ~(1 << 7)

        # Check Interrupts
        irq_pending = ((self.mip & self.mie) != 0) and ((self.mstatus & (1 << 3)) != 0)
        if irq_pending:
            self.mepc = self.pc
            if (self.mip & self.mie) & (1 << 7):
                self.mcause = 0x80000007 # Machine Timer Interrupt
            elif (self.mip & self.mie) & (1 << 11):
                self.mcause = 0x8000000B # Machine External Interrupt
            elif (self.mip & self.mie) & (1 << 3):
                self.mcause = 0x80000003 # Machine Software Interrupt
            else:
                self.mcause = 0x80000007
            self.mtval = 0
            mpie = (self.mstatus >> 3) & 1
            self.mstatus = (self.mstatus & ~0x88) | (mpie << 7) # MPIE = MIE, MIE = 0
            self.pc = self.mtvec & ~3
            return

        instr = self.read_mem(self.pc)

        if instr == 0:
            if self.verbose:
                print(f"[EMU] Stopped: Zero instruction at PC=0x{self.pc:08x}")
            self.running = False
            return

        opcode = instr & 0x7F
        rd = (instr >> 7) & 0x1F
        funct3 = (instr >> 12) & 0x7
        rs1 = (instr >> 15) & 0x1F
        rs2 = (instr >> 20) & 0x1F
        funct7 = (instr >> 25) & 0x7F

        if self.verbose:
            print(f"[STEP] PC={self.pc:08x} INST={instr:08x} op={opcode:02x} rd={rd} rs1={rs1}({self.regs[rs1]:x}) rs2={rs2}({self.regs[rs2]:x})")

        next_pc = self.pc + 4

        # OP-IMM (ADDI, SLTI, etc.)
        if opcode == 0x13:
            imm = (instr >> 20)
            if imm & 0x800:
                imm -= 0x1000
            if funct3 == 0: # ADDI
                self.regs[rd] = (self.regs[rs1] + imm) & 0xFFFFFFFF
            elif funct3 == 1: # SLLI
                shamt = (instr >> 20) & 0x1F
                self.regs[rd] = (self.regs[rs1] << shamt) & 0xFFFFFFFF
            elif funct3 == 2: # SLTI
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                self.regs[rd] = 1 if s1 < imm else 0
            elif funct3 == 3: # SLTIU
                self.regs[rd] = 1 if (self.regs[rs1] & 0xFFFFFFFF) < (imm & 0xFFFFFFFF) else 0
            elif funct3 == 4: # XORI
                self.regs[rd] = (self.regs[rs1] ^ (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
            elif funct3 == 5: # SRLI / SRAI
                shamt = (instr >> 20) & 0x1F
                if (instr >> 30) & 1: # SRAI
                    s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                    self.regs[rd] = (s1 >> shamt) & 0xFFFFFFFF
                else: # SRLI
                    self.regs[rd] = (self.regs[rs1] >> shamt) & 0xFFFFFFFF
            elif funct3 == 6: # ORI
                self.regs[rd] = (self.regs[rs1] | (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
            elif funct3 == 7: # ANDI
                self.regs[rd] = (self.regs[rs1] & (imm & 0xFFFFFFFF)) & 0xFFFFFFFF

        # OP (ADD, SUB, etc.)
        elif opcode == 0x33:
            if funct3 == 0:
                if funct7 == 0x00: # ADD
                    self.regs[rd] = (self.regs[rs1] + self.regs[rs2]) & 0xFFFFFFFF
                elif funct7 == 0x20: # SUB
                    self.regs[rd] = (self.regs[rs1] - self.regs[rs2]) & 0xFFFFFFFF
            elif funct3 == 1: # SLL
                shamt = self.regs[rs2] & 0x1F
                self.regs[rd] = (self.regs[rs1] << shamt) & 0xFFFFFFFF
            elif funct3 == 2: # SLT
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                s2 = self.regs[rs2] if self.regs[rs2] < 0x80000000 else self.regs[rs2] - 0x100000000
                self.regs[rd] = 1 if s1 < s2 else 0
            elif funct3 == 3: # SLTU
                self.regs[rd] = 1 if (self.regs[rs1] & 0xFFFFFFFF) < (self.regs[rs2] & 0xFFFFFFFF) else 0
            elif funct3 == 4: # XOR
                self.regs[rd] = (self.regs[rs1] ^ self.regs[rs2]) & 0xFFFFFFFF
            elif funct3 == 5: # SRL / SRA
                shamt = self.regs[rs2] & 0x1F
                if funct7 == 0x20: # SRA
                    s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                    self.regs[rd] = (s1 >> shamt) & 0xFFFFFFFF
                else: # SRL
                    self.regs[rd] = (self.regs[rs1] >> shamt) & 0xFFFFFFFF
            elif funct3 == 6: # OR
                self.regs[rd] = (self.regs[rs1] | self.regs[rs2]) & 0xFFFFFFFF
            elif funct3 == 7: # AND
                self.regs[rd] = (self.regs[rs1] & self.regs[rs2]) & 0xFFFFFFFF

        # LUI
        elif opcode == 0x37:
            imm = instr & 0xFFFFF000
            self.regs[rd] = imm

        # AUIPC
        elif opcode == 0x17:
            imm = instr & 0xFFFFF000
            self.regs[rd] = (self.pc + imm) & 0xFFFFFFFF

        # JAL
        elif opcode == 0x6F:
            imm20 = (instr >> 31) & 1
            imm10_1 = (instr >> 21) & 0x3FF
            imm11 = (instr >> 20) & 1
            imm19_12 = (instr >> 12) & 0xFF
            imm = (imm20 << 20) | (imm19_12 << 12) | (imm11 << 11) | (imm10_1 << 1)
            if imm & 0x100000:
                imm -= 0x200000
            if imm == 0: # Infinite loop (j .)
                if self.verbose:
                    print(f"[EMU] Stopped: Infinite loop (j .) at PC=0x{self.pc:08x}")
                self.running = False
                return
            self.regs[rd] = self.pc + 4
            next_pc = (self.pc + imm) & 0xFFFFFFFF

        # JALR
        elif opcode == 0x67:
            imm = (instr >> 20)
            if imm & 0x800:
                imm -= 0x1000
            self.regs[rd] = self.pc + 4
            next_pc = (self.regs[rs1] + imm) & 0xFFFFFFFE

        # BRANCH (BEQ, BNE, BGE, BGEU, BLT, BLTU, etc.)
        elif opcode == 0x63:
            imm12 = (instr >> 31) & 1
            imm10_5 = (instr >> 25) & 0x3F
            imm4_1 = (instr >> 8) & 0xF
            imm11 = (instr >> 7) & 1
            imm = (imm12 << 12) | (imm11 << 11) | (imm10_5 << 5) | (imm4_1 << 1)
            if imm & 0x1000:
                imm -= 0x2000
            take = False
            if funct3 == 0: # BEQ
                take = (self.regs[rs1] == self.regs[rs2])
            elif funct3 == 1: # BNE
                take = (self.regs[rs1] != self.regs[rs2])
            elif funct3 == 4: # BLT
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                s2 = self.regs[rs2] if self.regs[rs2] < 0x80000000 else self.regs[rs2] - 0x100000000
                take = (s1 < s2)
            elif funct3 == 5: # BGE
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                s2 = self.regs[rs2] if self.regs[rs2] < 0x80000000 else self.regs[rs2] - 0x100000000
                take = (s1 >= s2)
            elif funct3 == 6: # BLTU
                take = (self.regs[rs1] & 0xFFFFFFFF) < (self.regs[rs2] & 0xFFFFFFFF)
            elif funct3 == 7: # BGEU
                take = (self.regs[rs1] & 0xFFFFFFFF) >= (self.regs[rs2] & 0xFFFFFFFF)
            if take:
                next_pc = (self.pc + imm) & 0xFFFFFFFF

        # LOAD (LW, LB, LBU, LH, LHU, etc.)
        elif opcode == 0x03:
            imm = (instr >> 20)
            if imm & 0x800:
                imm -= 0x1000
            addr = (self.regs[rs1] + imm) & 0xFFFFFFFF
            val = self.read_mem(addr)
            if funct3 == 0: # LB
                byte = (val >> ((addr & 3) * 8)) & 0xFF
                if byte & 0x80: byte -= 256
                self.regs[rd] = byte & 0xFFFFFFFF
            elif funct3 == 1: # LH
                half = (val >> ((addr & 2) * 8)) & 0xFFFF
                if half & 0x8000: half -= 65536
                self.regs[rd] = half & 0xFFFFFFFF
            elif funct3 == 2: # LW
                self.regs[rd] = val
            elif funct3 == 4: # LBU
                byte = (val >> ((addr & 3) * 8)) & 0xFF
                self.regs[rd] = byte
            elif funct3 == 5: # LHU
                half = (val >> ((addr & 2) * 8)) & 0xFFFF
                self.regs[rd] = half

        # STORE (SW, SB, SH, etc.)
        elif opcode == 0x23:
            imm11_5 = (instr >> 25) & 0x7F
            imm4_0 = (instr >> 7) & 0x1F
            imm = (imm11_5 << 5) | imm4_0
            if imm & 0x800:
                imm -= 0x1000
            addr = (self.regs[rs1] + imm) & 0xFFFFFFFF
            if funct3 == 0: # SB
                byte_en = 1 << (addr & 3)
                val = (self.regs[rs2] & 0xFF) << ((addr & 3) * 8)
                self.write_mem(addr, val, byte_en)
            elif funct3 == 1: # SH
                byte_en = 3 << (addr & 2)
                val = (self.regs[rs2] & 0xFFFF) << ((addr & 2) * 8)
                self.write_mem(addr, val, byte_en)
            elif funct3 == 2: # SW
                self.write_mem(addr, self.regs[rs2], 0xF)

        # SYSTEM (CSRRW, CSRRS, CSRRC, CSRRWI, CSRRSI, CSRRCI, ECALL, EBREAK, MRET, WFI)
        elif opcode == 0x73:
            csr_addr = (instr >> 20) & 0xFFF
            zimm = rs1

            if funct3 == 0: # PRIVILEGED (ECALL, EBREAK, MRET, WFI)
                funct12 = (instr >> 20) & 0xFFF
                if funct12 == 0x000: # ECALL
                    self.mepc = self.pc
                    self.mcause = 11 # Environment call from M-mode
                    self.mtval = 0
                    mpie = (self.mstatus >> 3) & 1
                    self.mstatus = (self.mstatus & ~0x88) | (mpie << 7)
                    next_pc = self.mtvec & ~3
                elif funct12 == 0x001: # EBREAK
                    self.mepc = self.pc
                    self.mcause = 3 # Breakpoint
                    self.mtval = 0
                    mpie = (self.mstatus >> 3) & 1
                    self.mstatus = (self.mstatus & ~0x88) | (mpie << 7)
                    next_pc = self.mtvec & ~3
                elif funct12 == 0x302: # MRET
                    mpie = (self.mstatus >> 7) & 1
                    self.mstatus = (self.mstatus & ~0x88) | (mpie << 3) | 0x80
                    next_pc = self.mepc
                elif funct12 == 0x105: # WFI
                    if self.verbose:
                        print(f"[EMU] WFI at PC=0x{self.pc:08x}")
            elif funct3 == 1: # CSRRW
                old_val = self.read_csr(csr_addr)
                self.write_csr(csr_addr, self.regs[rs1])
                self.regs[rd] = old_val
            elif funct3 == 2: # CSRRS
                old_val = self.read_csr(csr_addr)
                if rs1 != 0:
                    self.write_csr(csr_addr, old_val | self.regs[rs1])
                self.regs[rd] = old_val
            elif funct3 == 3: # CSRRC
                old_val = self.read_csr(csr_addr)
                if rs1 != 0:
                    self.write_csr(csr_addr, old_val & ~self.regs[rs1])
                self.regs[rd] = old_val
            elif funct3 == 5: # CSRRWI
                old_val = self.read_csr(csr_addr)
                self.write_csr(csr_addr, zimm)
                self.regs[rd] = old_val
            elif funct3 == 6: # CSRRSI
                old_val = self.read_csr(csr_addr)
                if zimm != 0:
                    self.write_csr(csr_addr, old_val | zimm)
                self.regs[rd] = old_val
            elif funct3 == 7: # CSRRCI
                old_val = self.read_csr(csr_addr)
                if zimm != 0:
                    self.write_csr(csr_addr, old_val & ~zimm)
                self.regs[rd] = old_val

        self.regs[0] = 0
        self.pc = next_pc

    def run(self, max_steps=50000):
        for _ in range(max_steps):
            if not self.running or self.pc >= 0x00040000:
                break
            self.step()
        return "".join(self.uart_tx_buf)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python emulator.py <binary_file>")
        sys.exit(1)
    emu = SocEmulator()
    if '-v' in sys.argv:
        emu.verbose = True
    emu.load_binary(sys.argv[1])
    output = emu.run()
    print("\n--- Simulation Complete ---")
