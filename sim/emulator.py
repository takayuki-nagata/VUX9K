#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

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
        self.uart_rx_buf = []
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
        self.is_riscv_mode = None

    def load_binary(self, filename):
        with open(filename, 'rb') as f:
            data = f.read()
        if len(data) >= 4:
            rv_opcode = data[0] & 0x7F
            rv_valid = (data[0] & 3 == 3) and rv_opcode in (0x33, 0x13, 0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73)
            is_hack = not rv_valid

        self.is_riscv_mode = not is_hack

        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4:
                chunk = chunk + b'\x00' * (4 - len(chunk))
            if is_hack:
                instr0 = (chunk[0] << 8) | chunk[1]
                instr1 = (chunk[2] << 8) | chunk[3]
                word = (instr1 << 16) | instr0
            else:
                word = struct.unpack('<I', chunk)[0]
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
                if self.uart_rx_buf:
                    return ord(self.uart_rx_buf.pop(0)) & 0xFF
                return 0
            elif offset == 0x4:
                status = 0
                if not self.uart_rx_buf:
                    status |= 1 # STATUS_RX_EMPTY (1 << 0)
                return status
        elif 0x40001000 <= addr < 0x40010000:
            if addr == 0x40005000 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x8):
                return self.mtimecmp & 0xFFFFFFFF
            elif addr == 0x40005004 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0xC):
                return (self.mtimecmp >> 32) & 0xFFFFFFFF
            elif addr in (0x4000BFF8, 0x4000CFF8) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x0):
                return self.mtime & 0xFFFFFFFF
            elif addr in (0x4000BFFC, 0x4000CFFC) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x4):
                return (self.mtime >> 32) & 0xFFFFFFFF
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
        elif 0x40001000 <= addr < 0x40010000:
            if addr == 0x40005000 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x8):
                self.mtimecmp = (self.mtimecmp & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
            elif addr == 0x40005004 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0xC):
                self.mtimecmp = (self.mtimecmp & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
            elif addr in (0x4000BFF8, 0x4000CFF8) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x0):
                self.mtime = (self.mtime & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
            elif addr in (0x4000BFFC, 0x4000CFFC) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x4):
                self.mtime = (self.mtime & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
        elif 0x40002000 <= addr < 0x40002010:
            offset = addr & 0xF
            if offset == 0x4:
                self.sd_cs = val & 1

    def detect_mode(self):
        if self.is_riscv_mode is not None:
            return self.is_riscv_mode
        pc = self.pc
        w_idx = (pc >> 2) if (pc >= 4 or (pc > 0 and pc % 4 == 0)) else 0
        first_word = self.i_ram[w_idx] if w_idx < len(self.i_ram) else self.i_ram[0]
        opcode = first_word & 0x7F
        if (first_word & 3 == 3) and opcode in (0x33, 0x13, 0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73):
            self.is_riscv_mode = True
        elif first_word == 0 and pc != 0:
            self.is_riscv_mode = True
        else:
            self.is_riscv_mode = False
        return self.is_riscv_mode

    def step_hack(self):
        self.regs[0] = 0
        pc = self.pc
        w_idx = pc >> 2
        if w_idx >= len(self.i_ram):
            self.running = False
            return
        word = self.i_ram[w_idx]
        instr_16 = (word >> 16) & 0xFFFF if (pc & 2) else (word & 0xFFFF)

        if (instr_16 & 0x8000) == 0:  # A-instruction (@val)
            self.regs[1] = instr_16 & 0x7FFF
            self.pc += 2
        else:  # C-instruction (111 a c1..c6 d1..d3 j1..j3)
            a_bit = (instr_16 >> 12) & 1
            c_code = (instr_16 >> 6) & 0x3F
            d_code = (instr_16 >> 3) & 7
            j_code = instr_16 & 7

            D = self.regs[2] & 0xFFFF
            A = self.regs[1] & 0xFFFF
            if a_bit:
                if A == 24576:
                    M = ord(self.uart_rx_buf.pop(0)) & 0xFF if self.uart_rx_buf else 0
                elif A < len(self.d_ram):
                    M = self.d_ram[A] & 0xFFFF
                elif (A >> 1) < len(self.i_ram):
                    w = self.i_ram[A >> 1]
                    M = ((w >> 16) & 0xFFFF) if (A & 1) else (w & 0xFFFF)
                else:
                    M = 0
                Y = M
            else:
                Y = A

            if c_code == 0b101010: out = 0
            elif c_code == 0b111111: out = 1
            elif c_code == 0b111010: out = 0xFFFF
            elif c_code == 0b001100: out = D
            elif c_code == 0b110000: out = Y
            elif c_code == 0b001101: out = (~D) & 0xFFFF
            elif c_code == 0b110001: out = (~Y) & 0xFFFF
            elif c_code == 0b001111: out = (-D) & 0xFFFF
            elif c_code == 0b110011: out = (-Y) & 0xFFFF
            elif c_code == 0b011111: out = (D + 1) & 0xFFFF
            elif c_code == 0b110111: out = (Y + 1) & 0xFFFF
            elif c_code == 0b001110: out = (D - 1) & 0xFFFF
            elif c_code == 0b110010: out = (Y - 1) & 0xFFFF
            elif c_code == 0b000010: out = (D + Y) & 0xFFFF
            elif c_code == 0b010011: out = (D - Y) & 0xFFFF
            elif c_code == 0b000111: out = (Y - D) & 0xFFFF
            elif c_code == 0b000000: out = D & Y
            elif c_code == 0b010101: out = D | Y
            else: out = 0

            if d_code & 4:
                self.regs[1] = out
            if d_code & 2:
                self.regs[2] = out
            if d_code & 1:
                if A == 24576:
                    c_byte = out & 0xFF
                    if c_byte != 0:
                        char = chr(c_byte)
                        self.uart_tx_buf.append(char)
                        sys.stdout.write(char)
                        sys.stdout.flush()
                elif A < len(self.d_ram):
                    self.d_ram[A] = out

            signed_out = out if out < 0x8000 else out - 0x10000
            jump = False
            if j_code == 1: jump = (signed_out > 0)
            elif j_code == 2: jump = (signed_out == 0)
            elif j_code == 3: jump = (signed_out >= 0)
            elif j_code == 4: jump = (signed_out < 0)
            elif j_code == 5: jump = (signed_out != 0)
            elif j_code == 6: jump = (signed_out <= 0)
            elif j_code == 7: jump = True

            if jump:
                target = (self.regs[1] << 1)
                if target == pc or target == pc - 2:
                    self.running = False
                self.pc = target
            else:
                self.pc += 2

    def run_hack(self, max_steps=500000, target_str=None):
        i_ram = self.i_ram
        d_ram = self.d_ram
        regs = self.regs
        uart_rx_buf = self.uart_rx_buf
        uart_tx_buf = self.uart_tx_buf
        steps = 0
        i_ram_len = len(i_ram)
        d_ram_len = len(d_ram)

        while self.running and steps < max_steps:
            steps += 1
            pc = self.pc
            w_idx = pc >> 2
            if w_idx >= i_ram_len:
                self.running = False
                break
            word = i_ram[w_idx]
            instr_16 = (word >> 16) & 0xFFFF if (pc & 2) else (word & 0xFFFF)

            if (instr_16 & 0x8000) == 0:  # A-instruction (@val)
                regs[1] = instr_16 & 0x7FFF
                self.pc += 2
            else:  # C-instruction (111 a c1..c6 d1..d3 j1..j3)
                a_bit = (instr_16 >> 12) & 1
                c_code = (instr_16 >> 6) & 0x3F
                d_code = (instr_16 >> 3) & 7
                j_code = instr_16 & 7

                D = regs[2] & 0xFFFF
                A = regs[1] & 0xFFFF
                if a_bit:
                    if A == 24576:
                        M = ord(uart_rx_buf.pop(0)) & 0xFF if uart_rx_buf else 0
                    elif A < d_ram_len:
                        M = d_ram[A] & 0xFFFF
                    elif (A >> 1) < i_ram_len:
                        w = i_ram[A >> 1]
                        M = ((w >> 16) & 0xFFFF) if (A & 1) else (w & 0xFFFF)
                    else:
                        M = 0
                    Y = M
                else:
                    Y = A

                if c_code == 0b101010: out = 0
                elif c_code == 0b111111: out = 1
                elif c_code == 0b111010: out = 0xFFFF
                elif c_code == 0b001100: out = D
                elif c_code == 0b110000: out = Y
                elif c_code == 0b001101: out = (~D) & 0xFFFF
                elif c_code == 0b110001: out = (~Y) & 0xFFFF
                elif c_code == 0b001111: out = (-D) & 0xFFFF
                elif c_code == 0b110011: out = (-Y) & 0xFFFF
                elif c_code == 0b011111: out = (D + 1) & 0xFFFF
                elif c_code == 0b110111: out = (Y + 1) & 0xFFFF
                elif c_code == 0b001110: out = (D - 1) & 0xFFFF
                elif c_code == 0b110010: out = (Y - 1) & 0xFFFF
                elif c_code == 0b000010: out = (D + Y) & 0xFFFF
                elif c_code == 0b010011: out = (D - Y) & 0xFFFF
                elif c_code == 0b000111: out = (Y - D) & 0xFFFF
                elif c_code == 0b000000: out = D & Y
                elif c_code == 0b010101: out = D | Y
                else: out = 0

                if d_code & 4:
                    regs[1] = out
                if d_code & 2:
                    regs[2] = out
                if d_code & 1:
                    if A == 24576:
                        c_byte = out & 0xFF
                        if c_byte != 0:
                            char = chr(c_byte)
                            uart_tx_buf.append(char)
                            sys.stdout.write(char)
                            sys.stdout.flush()
                    elif A < d_ram_len:
                        d_ram[A] = out

                signed_out = out if out < 0x8000 else out - 0x10000
                jump = False
                if j_code == 1: jump = (signed_out > 0)
                elif j_code == 2: jump = (signed_out == 0)
                elif j_code == 3: jump = (signed_out >= 0)
                elif j_code == 4: jump = (signed_out < 0)
                elif j_code == 5: jump = (signed_out != 0)
                elif j_code == 6: jump = (signed_out <= 0)
                elif j_code == 7: jump = True

                if jump:
                    target = (regs[1] << 1)
                    if target == pc or target == pc - 2:
                        self.running = False
                    self.pc = target
                else:
                    self.pc += 2

            if target_str and len(uart_tx_buf) >= len(target_str):
                if target_str in "".join(uart_tx_buf[-len(target_str)*2:]):
                    break
        return "".join(uart_tx_buf)

    def step(self):
        if not self.detect_mode():
            return self.step_hack()
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
            if funct7 == 0x01: # M extension
                s1 = self.regs[rs1] if self.regs[rs1] < 0x80000000 else self.regs[rs1] - 0x100000000
                s2 = self.regs[rs2] if self.regs[rs2] < 0x80000000 else self.regs[rs2] - 0x100000000
                u1 = self.regs[rs1] & 0xFFFFFFFF
                u2 = self.regs[rs2] & 0xFFFFFFFF
                if funct3 == 0: # MUL
                    self.regs[rd] = (u1 * u2) & 0xFFFFFFFF
                elif funct3 == 1: # MULH
                    self.regs[rd] = ((s1 * s2) >> 32) & 0xFFFFFFFF
                elif funct3 == 2: # MULHSU
                    self.regs[rd] = ((s1 * u2) >> 32) & 0xFFFFFFFF
                elif funct3 == 3: # MULHU
                    self.regs[rd] = ((u1 * u2) >> 32) & 0xFFFFFFFF
                elif funct3 == 4: # DIV
                    if s2 == 0: self.regs[rd] = 0xFFFFFFFF
                    elif s1 == -0x80000000 and s2 == -1: self.regs[rd] = 0x80000000
                    else: self.regs[rd] = int(s1 / s2) & 0xFFFFFFFF
                elif funct3 == 5: # DIVU
                    if u2 == 0: self.regs[rd] = 0xFFFFFFFF
                    else: self.regs[rd] = (u1 // u2) & 0xFFFFFFFF
                elif funct3 == 6: # REM
                    if s2 == 0: self.regs[rd] = u1
                    elif s1 == -0x80000000 and s2 == -1: self.regs[rd] = 0
                    else:
                        rem = abs(s1) % abs(s2)
                        if s1 < 0: rem = -rem
                        self.regs[rd] = rem & 0xFFFFFFFF
                elif funct3 == 7: # REMU
                    if u2 == 0: self.regs[rd] = u1
                    else: self.regs[rd] = (u1 % u2) & 0xFFFFFFFF
            elif funct3 == 0:
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
            target = (self.regs[rs1] + imm) & 0xFFFFFFFE
            self.regs[rd] = self.pc + 4
            next_pc = target

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

    def feed_input(self, text):
        for ch in text:
            self.uart_rx_buf.append(ch)

    def get_tx_output(self):
        return "".join(self.uart_tx_buf)

    def clear_tx_output(self):
        self.uart_tx_buf.clear()

    def run(self, max_steps=50000, target_str=None):
        if not self.detect_mode():
            return self.run_hack(max_steps=max_steps, target_str=target_str)

        # Fast execution path when verbose is disabled
        if not self.verbose:
            i_ram = self.i_ram
            d_ram = self.d_ram
            regs = self.regs
            pc = self.pc
            mtime = self.mtime
            mtimecmp = self.mtimecmp
            mstatus = self.mstatus
            misa = self.misa
            mie = self.mie
            mtvec = self.mtvec
            mscratch = self.mscratch
            mepc = self.mepc
            mcause = self.mcause
            mtval = self.mtval
            mip = self.mip
            uart_rx_buf = self.uart_rx_buf
            uart_tx_buf = self.uart_tx_buf
            sd_cs = self.sd_cs

            steps = 0
            while self.running and steps < max_steps:
                regs[0] = 0
                mtime += 1
                if mtime >= mtimecmp:
                    mip |= 0x80
                else:
                    mip &= ~0x80

                if (mip & mie) and (mstatus & 8):
                    mepc = pc
                    if (mip & mie) & 0x80:
                        mcause = 0x80000007
                    elif (mip & mie) & 0x800:
                        mcause = 0x8000000B
                    elif (mip & mie) & 0x08:
                        mcause = 0x80000003
                    else:
                        mcause = 0x80000007
                    mtval = 0
                    mpie = (mstatus >> 3) & 1
                    mstatus = (mstatus & ~0x88) | (mpie << 7)
                    pc = mtvec & ~3
                    steps += 1
                    continue

                if pc < 0x40000:
                    instr = i_ram[pc >> 2]
                elif 0x20000000 <= pc < 0x20020000:
                    instr = d_ram[(pc & 0x1FFFF) >> 2]
                else:
                    break

                if instr == 0:
                    self.running = False
                    break

                opcode = instr & 0x7F
                rd = (instr >> 7) & 0x1F
                funct3 = (instr >> 12) & 0x7
                rs1 = (instr >> 15) & 0x1F
                rs2 = (instr >> 20) & 0x1F

                if opcode == 0x13: # OP-IMM
                    imm = instr >> 20
                    if imm & 0x800: imm -= 0x1000
                    if funct3 == 0:
                        regs[rd] = (regs[rs1] + imm) & 0xFFFFFFFF
                    elif funct3 == 1:
                        regs[rd] = (regs[rs1] << (imm & 0x1F)) & 0xFFFFFFFF
                    elif funct3 == 2:
                        s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                        regs[rd] = 1 if s1 < imm else 0
                    elif funct3 == 3:
                        regs[rd] = 1 if (regs[rs1] & 0xFFFFFFFF) < (imm & 0xFFFFFFFF) else 0
                    elif funct3 == 4:
                        regs[rd] = (regs[rs1] ^ (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
                    elif funct3 == 5:
                        shamt = imm & 0x1F
                        if (instr >> 30) & 1:
                            s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                            regs[rd] = (s1 >> shamt) & 0xFFFFFFFF
                        else:
                            regs[rd] = (regs[rs1] >> shamt) & 0xFFFFFFFF
                    elif funct3 == 6:
                        regs[rd] = (regs[rs1] | (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
                    elif funct3 == 7:
                        regs[rd] = (regs[rs1] & (imm & 0xFFFFFFFF)) & 0xFFFFFFFF
                    pc += 4

                elif opcode == 0x33: # OP
                    funct7 = (instr >> 25) & 0x7F
                    if funct7 == 0x01: # M extension
                        s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                        s2 = regs[rs2] if regs[rs2] < 0x80000000 else regs[rs2] - 0x100000000
                        u1 = regs[rs1] & 0xFFFFFFFF
                        u2 = regs[rs2] & 0xFFFFFFFF
                        if funct3 == 0: regs[rd] = (u1 * u2) & 0xFFFFFFFF
                        elif funct3 == 1: regs[rd] = ((s1 * s2) >> 32) & 0xFFFFFFFF
                        elif funct3 == 2: regs[rd] = ((s1 * u2) >> 32) & 0xFFFFFFFF
                        elif funct3 == 3: regs[rd] = ((u1 * u2) >> 32) & 0xFFFFFFFF
                        elif funct3 == 4:
                            if s2 == 0: regs[rd] = 0xFFFFFFFF
                            elif s1 == -0x80000000 and s2 == -1: regs[rd] = 0x80000000
                            else: regs[rd] = int(s1 / s2) & 0xFFFFFFFF
                        elif funct3 == 5:
                            if u2 == 0: regs[rd] = 0xFFFFFFFF
                            else: regs[rd] = (u1 // u2) & 0xFFFFFFFF
                        elif funct3 == 6:
                            if s2 == 0: regs[rd] = u1
                            elif s1 == -0x80000000 and s2 == -1: regs[rd] = 0
                            else:
                                rem = abs(s1) % abs(s2)
                                if s1 < 0: rem = -rem
                                regs[rd] = rem & 0xFFFFFFFF
                        elif funct3 == 7:
                            if u2 == 0: regs[rd] = u1
                            else: regs[rd] = (u1 % u2) & 0xFFFFFFFF
                    elif funct3 == 0:
                        if funct7 == 0x20:
                            regs[rd] = (regs[rs1] - regs[rs2]) & 0xFFFFFFFF
                        else:
                            regs[rd] = (regs[rs1] + regs[rs2]) & 0xFFFFFFFF
                    elif funct3 == 1:
                        regs[rd] = (regs[rs1] << (regs[rs2] & 0x1F)) & 0xFFFFFFFF
                    elif funct3 == 2:
                        s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                        s2 = regs[rs2] if regs[rs2] < 0x80000000 else regs[rs2] - 0x100000000
                        regs[rd] = 1 if s1 < s2 else 0
                    elif funct3 == 3:
                        regs[rd] = 1 if (regs[rs1] & 0xFFFFFFFF) < (regs[rs2] & 0xFFFFFFFF) else 0
                    elif funct3 == 4:
                        regs[rd] = (regs[rs1] ^ regs[rs2]) & 0xFFFFFFFF
                    elif funct3 == 5:
                        shamt = regs[rs2] & 0x1F
                        if funct7 == 0x20:
                            s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                            regs[rd] = (s1 >> shamt) & 0xFFFFFFFF
                        else:
                            regs[rd] = (regs[rs1] >> shamt) & 0xFFFFFFFF
                    elif funct3 == 6:
                        regs[rd] = (regs[rs1] | regs[rs2]) & 0xFFFFFFFF
                    elif funct3 == 7:
                        regs[rd] = (regs[rs1] & regs[rs2]) & 0xFFFFFFFF
                    pc += 4

                elif opcode == 0x23: # STORE
                    imm = ((instr >> 25) << 5) | ((instr >> 7) & 0x1F)
                    if imm & 0x800: imm -= 0x1000
                    addr = (regs[rs1] + imm) & 0xFFFFFFFF
                    val = regs[rs2]
                    if 0x20000000 <= addr < 0x20020000:
                        idx = (addr & 0x1FFFF) >> 2
                        old = d_ram[idx]
                        byte_offset = addr & 3
                        if funct3 == 0:
                            mask = 0xFF << (byte_offset * 8)
                            d_ram[idx] = (old & ~mask) | ((val & 0xFF) << (byte_offset * 8))
                        elif funct3 == 1:
                            mask = 0xFFFF << (byte_offset * 8)
                            d_ram[idx] = (old & ~mask) | ((val & 0xFFFF) << (byte_offset * 8))
                        elif funct3 == 2:
                            d_ram[idx] = val & 0xFFFFFFFF
                    elif addr == 0x40000000:
                        c_byte = val & 0xFF
                        if c_byte != 0:
                            char = chr(c_byte)
                            uart_tx_buf.append(char)
                            sys.stdout.write(char)
                            sys.stdout.flush()
                    elif 0x40001000 <= addr < 0x40010000:
                        if addr == 0x40005000 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x8):
                            mtimecmp = (mtimecmp & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
                        elif addr == 0x40005004 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0xC):
                            mtimecmp = (mtimecmp & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
                        elif addr in (0x4000BFF8, 0x4000CFF8) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x0):
                            mtime = (mtime & 0xFFFFFFFF00000000) | (val & 0xFFFFFFFF)
                        elif addr in (0x4000BFFC, 0x4000CFFC) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x4):
                            mtime = (mtime & 0x00000000FFFFFFFF) | ((val & 0xFFFFFFFF) << 32)
                    elif 0x40002000 <= addr < 0x40002010:
                        offset = addr & 0xF
                        if offset == 0x4:
                            sd_cs = val & 1
                    pc += 4

                elif opcode == 0x03: # LOAD
                    imm = instr >> 20
                    if imm & 0x800: imm -= 0x1000
                    addr = (regs[rs1] + imm) & 0xFFFFFFFF
                    raw = 0
                    if addr < 0x40000:
                        raw = i_ram[addr >> 2]
                    elif 0x20000000 <= addr < 0x20020000:
                        raw = d_ram[(addr & 0x1FFFF) >> 2]
                    elif 0x40000000 <= addr < 0x40000010:
                        offset = addr & 0xF
                        if offset == 0x0:
                            if uart_rx_buf:
                                raw = ord(uart_rx_buf.pop(0)) & 0xFF
                            else:
                                raw = 0
                        elif offset == 0x4:
                            raw = 1 if not uart_rx_buf else 0
                    elif 0x40001000 <= addr < 0x40010000:
                        if addr == 0x40005000 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x8):
                            raw = mtimecmp & 0xFFFFFFFF
                        elif addr == 0x40005004 or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0xC):
                            raw = (mtimecmp >> 32) & 0xFFFFFFFF
                        elif addr in (0x4000BFF8, 0x4000CFF8) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x0):
                            raw = mtime & 0xFFFFFFFF
                        elif addr in (0x4000BFFC, 0x4000CFFC) or (0x40001000 <= addr < 0x40001010 and (addr & 0xF) == 0x4):
                            raw = (mtime >> 32) & 0xFFFFFFFF
                    elif 0x40002000 <= addr < 0x40002010:
                        offset = addr & 0xF
                        if offset == 0x0: raw = 0
                        elif offset == 0x4: raw = sd_cs
                        elif offset == 0x8: raw = 2
                    byte_offset = addr & 3
                    if funct3 == 0:
                        b = (raw >> (byte_offset * 8)) & 0xFF
                        regs[rd] = b if b < 0x80 else b - 0x100
                    elif funct3 == 1:
                        h = (raw >> (byte_offset * 8)) & 0xFFFF
                        regs[rd] = h if h < 0x8000 else h - 0x10000
                    elif funct3 == 2:
                        regs[rd] = raw & 0xFFFFFFFF
                    elif funct3 == 4:
                        regs[rd] = (raw >> (byte_offset * 8)) & 0xFF
                    elif funct3 == 5:
                        regs[rd] = (raw >> (byte_offset * 8)) & 0xFFFF
                    pc += 4

                elif opcode == 0x63: # BRANCH
                    imm12 = (instr >> 31) & 1
                    imm10_5 = (instr >> 25) & 0x3F
                    imm4_1 = (instr >> 8) & 0xF
                    imm11 = (instr >> 7) & 1
                    imm = (imm12 << 12) | (imm11 << 11) | (imm10_5 << 5) | (imm4_1 << 1)
                    if imm & 0x1000: imm -= 0x2000
                    take = False
                    s1 = regs[rs1] if regs[rs1] < 0x80000000 else regs[rs1] - 0x100000000
                    s2 = regs[rs2] if regs[rs2] < 0x80000000 else regs[rs2] - 0x100000000
                    if funct3 == 0: take = (regs[rs1] == regs[rs2])
                    elif funct3 == 1: take = (regs[rs1] != regs[rs2])
                    elif funct3 == 4: take = (s1 < s2)
                    elif funct3 == 5: take = (s1 >= s2)
                    elif funct3 == 6: take = ((regs[rs1] & 0xFFFFFFFF) < (regs[rs2] & 0xFFFFFFFF))
                    elif funct3 == 7: take = ((regs[rs1] & 0xFFFFFFFF) >= (regs[rs2] & 0xFFFFFFFF))
                    if take: pc = (pc + imm) & 0xFFFFFFFF
                    else: pc += 4

                elif opcode == 0x6F: # JAL
                    imm20 = (instr >> 31) & 1
                    imm10_1 = (instr >> 21) & 0x3FF
                    imm11 = (instr >> 20) & 1
                    imm19_12 = (instr >> 12) & 0xFF
                    imm = (imm20 << 20) | (imm19_12 << 12) | (imm11 << 11) | (imm10_1 << 1)
                    if imm & 0x100000: imm -= 0x200000
                    if imm == 0:
                        self.running = False
                        break
                    regs[rd] = pc + 4
                    pc = (pc + imm) & 0xFFFFFFFF

                elif opcode == 0x67: # JALR
                    imm = instr >> 20
                    if imm & 0x800: imm -= 0x1000
                    target = (regs[rs1] + imm) & 0xFFFFFFFE
                    regs[rd] = pc + 4
                    pc = target

                elif opcode == 0x37: # LUI
                    regs[rd] = instr & 0xFFFFF000
                    pc += 4

                elif opcode == 0x17: # AUIPC
                    regs[rd] = (pc + (instr & 0xFFFFF000)) & 0xFFFFFFFF
                    pc += 4

                elif opcode == 0x73: # SYSTEM (CSR / ECALL / EBREAK / MRET)
                    funct12 = instr >> 20
                    if funct3 == 0:
                        if funct12 == 0x302: # MRET
                            mpie = (mstatus >> 7) & 1
                            mstatus = (mstatus & ~0x88) | (mpie << 3) | 0x80
                            pc = mepc
                        elif funct12 == 0x000: # ECALL
                            mepc = pc
                            mcause = 11
                            mtval = 0
                            mpie = (mstatus >> 3) & 1
                            mstatus = (mstatus & ~0x88) | (mpie << 7)
                            pc = mtvec & ~3
                        elif funct12 == 0x001: # EBREAK
                            mepc = pc
                            mcause = 3
                            mtval = 0
                            mpie = (mstatus >> 3) & 1
                            mstatus = (mstatus & ~0x88) | (mpie << 7)
                            pc = mtvec & ~3
                        else: # WFI / FENCE / other
                            pc += 4
                    else:
                        csr_addr = funct12 & 0xFFF
                        val = 0
                        if csr_addr == 0x300: val = mstatus
                        elif csr_addr == 0x301: val = misa
                        elif csr_addr == 0x304: val = mie
                        elif csr_addr == 0x305: val = mtvec
                        elif csr_addr == 0x340: val = mscratch
                        elif csr_addr == 0x341: val = mepc
                        elif csr_addr == 0x342: val = mcause
                        elif csr_addr == 0x343: val = mtval
                        elif csr_addr == 0x344: val = mip
                        
                        src = regs[rs1] if funct3 in (1, 2, 3) else rs1
                        new_val = val
                        if funct3 in (1, 5): new_val = src
                        elif funct3 in (2, 6): new_val = val | src
                        elif funct3 in (3, 7): new_val = val & ~src

                        if csr_addr == 0x300: mstatus = (new_val & 0x88) | 0x1800
                        elif csr_addr == 0x304: mie = new_val & 0x888
                        elif csr_addr == 0x305: mtvec = new_val & ~3
                        elif csr_addr == 0x340: mscratch = new_val
                        elif csr_addr == 0x341: mepc = new_val & ~1
                        elif csr_addr == 0x342: mcause = new_val
                        elif csr_addr == 0x343: mtval = new_val
                        elif csr_addr == 0x344: mip = (mip & ~8) | (new_val & 8)

                        regs[rd] = val
                        pc += 4
                else:
                    self.running = False
                    break

                steps += 1
                if target_str and len(uart_rx_buf) == 0 and steps % 5000 == 0:
                    if target_str in "".join(uart_tx_buf):
                        break

            # Sync local variables back to instance
            self.pc = pc
            self.mtime = mtime
            self.mtimecmp = mtimecmp
            self.mstatus = mstatus
            self.mie = mie
            self.mtvec = mtvec
            self.mscratch = mscratch
            self.mepc = mepc
            self.mcause = mcause
            self.mtval = mtval
            self.mip = mip
            self.sd_cs = sd_cs
        else:
            for _ in range(max_steps):
                if not self.running or self.pc >= 0x00040000:
                    break
                self.step()
                if target_str and len(self.uart_rx_buf) == 0 and target_str in "".join(self.uart_tx_buf):
                    break

        return "".join(self.uart_tx_buf)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Tang Nano 9K Dual-ISA SoC Emulator")
    parser.add_argument("binary", help="Path to raw binary file (e.g. zephyr.bin)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose instruction tracing")
    parser.add_argument("--steps", type=int, default=100000, help="Maximum simulation steps (default: 100000)")
    parser.add_argument("--until", type=str, default=None, help="Stop when target string appears in UART output")
    args = parser.parse_args()

    emu = SocEmulator()
    emu.verbose = args.verbose
    emu.load_binary(args.binary)
    output = emu.run(max_steps=args.steps, target_str=args.until)
    print("\n--- Simulation Complete ---")
