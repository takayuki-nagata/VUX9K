#!/usr/bin/env python3
"""
Tang Nano 9K Dual-ISA SoC Behavioral Python Emulator
Simulates RV32I & Hack 16-bit CPU execution, 256KB I-RAM, 128KB D-RAM,
MMIO UART, MMIO Timer, and MMIO SD Card SPI controller.
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

    def load_binary(self, filename):
        with open(filename, 'rb') as f:
            data = f.read()
        for i in range(0, len(data), 4):
            if i + 4 <= len(data):
                word = struct.unpack('<I', data[i:i+4])[0]
                idx = i // 4
                if idx < len(self.i_ram):
                    self.i_ram[idx] = word

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
            elif funct3 == 2: # SLTI
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                self.regs[rd] = 1 if s1 < imm else 0
            elif funct3 == 3: # SLTIU
                self.regs[rd] = 1 if (self.regs[rs1] & 0xFFFFFFFF) < (imm & 0xFFFFFFFF) else 0
            elif funct3 == 4: # XORI
                self.regs[rd] = (self.regs[rs1] ^ (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
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
            elif funct3 == 4: # XOR
                self.regs[rd] = (self.regs[rs1] ^ self.regs[rs2]) & 0xFFFFFFFF
            elif funct3 == 5: # SRL / SRA
                shamt = self.regs[rs2] & 0x1F
                if funct7 == 0x20: # SRA
                    s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                    self.regs[rd] = (s1 >> shamt) & 0xFFFFFFFF
                else: # SRL
                    self.regs[rd] = (self.regs[rs1] >> shamt) & 0xFFFFFFFF

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

        # SYSTEM (CSRRW, CSRRS, ECALL, EBREAK, WFI)
        elif opcode == 0x73:
            if funct3 == 1 or funct3 == 2: # CSRRW / CSRRS
                csr = (instr >> 20) & 0xFFF
                if csr == 0x305: # mtvec
                    self.regs[rd] = 0
                elif csr == 0x300: # mstatus
                    self.regs[rd] = 0
            elif funct3 == 0: # WFI / ECALL / EBREAK
                if (instr >> 20) == 0x105: # WFI
                    if self.verbose:
                        print(f"[EMU] Stopped: WFI instruction at PC=0x{self.pc:08x}")
                    self.running = False
                    return

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
