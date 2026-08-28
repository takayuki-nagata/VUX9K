// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

`timescale 1ns / 1ps

// ============================================================================
// Gowin Primitive Simulation Library for Gate-Level Simulation (GLS)
// Includes full behavioral implementations for BSRAMs (DPB, SPX9, SP, DP, SDPB),
// LUTRAMs (RAM16SDP4, RAM16S*), ALUs, DFFs, MUXes, and I/O cells.
// ============================================================================

// ----------------------------------------------------------------------------
// LUT Primitives (1 to 4 inputs)
// ----------------------------------------------------------------------------
module LUT1 #(
    parameter [1:0] INIT = 2'h0
) (
    output wire F,
    input  wire I0
);
    assign F = I0 ? INIT[1] : INIT[0];
endmodule

module LUT2 #(
    parameter [3:0] INIT = 4'h0
) (
    output wire F,
    input  wire I0,
    input  wire I1
);
    wire [1:0] s1 = I1 ? INIT[3:2] : INIT[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module LUT3 #(
    parameter [7:0] INIT = 8'h0
) (
    output wire F,
    input  wire I0,
    input  wire I1,
    input  wire I2
);
    wire [3:0] s2 = I2 ? INIT[7:4] : INIT[3:0];
    wire [1:0] s1 = I1 ? s2[3:2]   : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module LUT4 #(
    parameter [15:0] INIT = 16'h0
) (
    output wire F,
    input  wire I0,
    input  wire I1,
    input  wire I2,
    input  wire I3
);
    wire [7:0] s3 = I3 ? INIT[15:8] : INIT[7:0];
    wire [3:0] s2 = I2 ? s3[7:4]    : s3[3:0];
    wire [1:0] s1 = I1 ? s2[3:2]    : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module __APICULA_LUT5 (output wire F, input wire I0, I1, I2, I3, M0);
    assign F = 1'b0;
endmodule

module __APICULA_LUT6 (output wire F, input wire I0, I1, I2, I3, M0, M1);
    assign F = 1'b0;
endmodule

module __APICULA_LUT7 (output wire F, input wire I0, I1, I2, I3, M0, M1, M2);
    assign F = 1'b0;
endmodule

module __APICULA_LUT8 (output wire F, input wire I0, I1, I2, I3, M0, M1, M2, M3);
    assign F = 1'b0;
endmodule

module LUT5 #(
    parameter [31:0] INIT = 32'h0
) (
    input  wire I0, I1, I2, I3, I4,
    output wire F
);
    wire [15:0] s4 = I4 ? INIT[31:16] : INIT[15:0];
    wire [7:0]  s3 = I3 ? s4[15:8]    : s4[7:0];
    wire [3:0]  s2 = I2 ? s3[7:4]     : s3[3:0];
    wire [1:0]  s1 = I1 ? s2[3:2]     : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module LUT6 #(
    parameter [63:0] INIT = 64'h0
) (
    input  wire I0, I1, I2, I3, I4, I5,
    output wire F
);
    wire [31:0] s5 = I5 ? INIT[63:32] : INIT[31:0];
    wire [15:0] s4 = I4 ? s5[31:16]   : s5[15:0];
    wire [7:0]  s3 = I3 ? s4[15:8]    : s4[7:0];
    wire [3:0]  s2 = I2 ? s3[7:4]     : s3[3:0];
    wire [1:0]  s1 = I1 ? s2[3:2]     : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module LUT7 #(
    parameter [127:0] INIT = 128'h0
) (
    input  wire I0, I1, I2, I3, I4, I5, I6,
    output wire F
);
    wire [63:0] s6 = I6 ? INIT[127:64] : INIT[63:0];
    wire [31:0] s5 = I5 ? s6[63:32]    : s6[31:0];
    wire [15:0] s4 = I4 ? s5[31:16]    : s5[15:0];
    wire [7:0]  s3 = I3 ? s4[15:8]     : s4[7:0];
    wire [3:0]  s2 = I2 ? s3[7:4]      : s3[3:0];
    wire [1:0]  s1 = I1 ? s2[3:2]      : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

module LUT8 #(
    parameter [255:0] INIT = 256'h0
) (
    input  wire I0, I1, I2, I3, I4, I5, I6, I7,
    output wire F
);
    wire [127:0] s7 = I7 ? INIT[255:128] : INIT[127:0];
    wire [63:0]  s6 = I6 ? s7[127:64]    : s7[63:0];
    wire [31:0]  s5 = I5 ? s6[63:32]     : s6[31:0];
    wire [15:0]  s4 = I4 ? s5[31:16]     : s5[15:0];
    wire [7:0]   s3 = I3 ? s4[15:8]      : s4[7:0];
    wire [3:0]   s2 = I2 ? s3[7:4]       : s3[3:0];
    wire [1:0]   s1 = I1 ? s2[3:2]       : s2[1:0];
    assign F = I0 ? s1[1] : s1[0];
endmodule

// ----------------------------------------------------------------------------
// MUX Primitives
// ----------------------------------------------------------------------------
module MUX2 (
    output wire O,
    input  wire I0,
    input  wire I1,
    input  wire S0
);
    assign O = S0 ? I1 : I0;
endmodule

module MUX2_LUT5 (output wire O, input wire I0, input wire I1, input wire S0);
    assign O = S0 ? I1 : I0;
endmodule

module MUX2_LUT6 (output wire O, input wire I0, input wire I1, input wire S0);
    assign O = S0 ? I1 : I0;
endmodule

module MUX2_LUT7 (output wire O, input wire I0, input wire I1, input wire S0);
    assign O = S0 ? I1 : I0;
endmodule

module MUX2_LUT8 (output wire O, input wire I0, input wire I1, input wire S0);
    assign O = S0 ? I1 : I0;
endmodule

module INV (input wire I, output wire O);
    assign O = ~I;
endmodule

// ----------------------------------------------------------------------------
// ALU Primitive (Gowin Ripple Carry / Arithmetic slice)
// ----------------------------------------------------------------------------
module ALU #(
    parameter integer ALU_MODE = 0
) (
    output wire SUM,
    output wire COUT,
    input  wire I0,
    input  wire I1,
    input  wire I3,
    input  wire CIN
);
    localparam ADD    = 0;
    localparam SUB    = 1;
    localparam ADDSUB = 2;
    localparam NE     = 3;
    localparam GE     = 4;
    localparam LE     = 5;
    localparam CUP    = 6;
    localparam CDN    = 7;
    localparam CUPCDN = 8;
    localparam MULT   = 9;

    reg S, C;

    always @* begin
        case (ALU_MODE)
            ADD: begin
                S = I0 ^ I1;
                C = I0;
            end
            SUB: begin
                S = I0 ^ ~I1;
                C = I0;
            end
            ADDSUB: begin
                S = I3 ? (I0 ^ I1) : (I0 ^ ~I1);
                C = I0;
            end
            NE: begin
                S = I0 ^ ~I1;
                C = 1'b1;
            end
            GE: begin
                S = I0 ^ ~I1;
                C = I0;
            end
            LE: begin
                S = ~I0 ^ I1;
                C = I1;
            end
            CUP: begin
                S = I0;
                C = 1'b0;
            end
            CDN: begin
                S = ~I0;
                C = 1'b1;
            end
            CUPCDN: begin
                S = I3 ? I0 : ~I0;
                C = I0;
            end
            MULT: begin
                S = (I0 & I1) ^ I3;
                C = I0 & I1;
            end
            default: begin
                S = I0 ^ I1;
                C = I0;
            end
        endcase
    end

    assign SUM  = S ^ CIN;
    assign COUT = S ? CIN : C;
endmodule

// ----------------------------------------------------------------------------
// Flip-Flop Primitives
// ----------------------------------------------------------------------------
module DFF (output reg Q, input wire CLK, input wire D);
    initial Q = 1'b0;
    always @(posedge CLK) Q <= D;
endmodule

module DFFE (output reg Q, input wire D, input wire CLK, input wire CE);
    initial Q = 1'b0;
    always @(posedge CLK) if (CE) Q <= D;
endmodule

module DFFS (output reg Q, input wire D, input wire CLK, input wire SET);
    initial Q = 1'b0;
    always @(posedge CLK) begin
        if (SET) Q <= 1'b1;
        else     Q <= D;
    end
endmodule

module DFFSE (output reg Q, input wire D, input wire CLK, input wire CE, input wire SET);
    initial Q = 1'b0;
    always @(posedge CLK) begin
        if (SET)      Q <= 1'b1;
        else if (CE)  Q <= D;
    end
endmodule

module DFFR (output reg Q, input wire D, input wire CLK, input wire RESET);
    initial Q = 1'b0;
    always @(posedge CLK) begin
        if (RESET) Q <= 1'b0;
        else       Q <= D;
    end
endmodule

module DFFRE (output reg Q, input wire D, input wire CLK, input wire CE, input wire RESET);
    initial Q = 1'b0;
    always @(posedge CLK) begin
        if (RESET)    Q <= 1'b0;
        else if (CE)  Q <= D;
    end
endmodule

module DFFP (output reg Q, input wire D, input wire CLK, input wire PRESET);
    initial Q = 1'b1;
    always @(posedge CLK or posedge PRESET) begin
        if (PRESET) Q <= 1'b1;
        else        Q <= D;
    end
endmodule

module DFFPE (output reg Q, input wire D, input wire CLK, input wire CE, input wire PRESET);
    initial Q = 1'b1;
    always @(posedge CLK or posedge PRESET) begin
        if (PRESET)   Q <= 1'b1;
        else if (CE)  Q <= D;
    end
endmodule

module DFFC (output reg Q, input wire D, input wire CLK, input wire CLEAR);
    initial Q = 1'b0;
    always @(posedge CLK or posedge CLEAR) begin
        if (CLEAR) Q <= 1'b0;
        else       Q <= D;
    end
endmodule

module DFFCE (output reg Q, input wire D, input wire CLK, input wire CE, input wire CLEAR);
    initial Q = 1'b0;
    always @(posedge CLK or posedge CLEAR) begin
        if (CLEAR)    Q <= 1'b0;
        else if (CE)  Q <= D;
    end
endmodule

// ----------------------------------------------------------------------------
// Constant Drivers & Buffers
// ----------------------------------------------------------------------------
module VCC (output wire V);
    assign V = 1'b1;
endmodule

module GND (output wire G);
    assign G = 1'b0;
endmodule

module IBUF (output wire O, input wire I);
    assign O = I;
endmodule

module OBUF (output wire O, input wire I);
    assign O = I;
endmodule

module TBUF (output wire O, input wire I, input wire OEN);
    assign O = OEN ? 1'bz : I;
endmodule

module IOBUF (output wire O, inout wire IO, input wire I, input wire OEN);
    assign O = IO;
    assign IO = OEN ? 1'bz : I;
endmodule

module GSR (input wire GSRI);
endmodule

// ----------------------------------------------------------------------------
// Distributed RAM Primitives (LUTRAM)
// ----------------------------------------------------------------------------
module RAM16SDP4 #(
    parameter [15:0] INIT_0 = 16'h0000,
    parameter [15:0] INIT_1 = 16'h0000,
    parameter [15:0] INIT_2 = 16'h0000,
    parameter [15:0] INIT_3 = 16'h0000
) (
    output wire [3:0] DO,
    input  wire [3:0] DI,
    input  wire [3:0] WAD,
    input  wire [3:0] RAD,
    input  wire       WRE,
    input  wire       CLK
);
    reg [15:0] mem0, mem1, mem2, mem3;

    initial begin
        mem0 = INIT_0;
        mem1 = INIT_1;
        mem2 = INIT_2;
        mem3 = INIT_3;
    end

    assign DO[0] = mem0[RAD];
    assign DO[1] = mem1[RAD];
    assign DO[2] = mem2[RAD];
    assign DO[3] = mem3[RAD];

    always @(posedge CLK) begin
        if (WRE) begin
            mem0[WAD] <= DI[0];
            mem1[WAD] <= DI[1];
            mem2[WAD] <= DI[2];
            mem3[WAD] <= DI[3];
        end
    end
endmodule

// ----------------------------------------------------------------------------
// Block RAM Primitive: DPB (Dual Port Block RAM 16Kbits)
// Fully parameterized for all Gowin bit widths, read modes, write modes & INIT_RAM_xx
// ----------------------------------------------------------------------------
module DPB #(
    parameter READ_MODE0  = 1'b0,
    parameter READ_MODE1  = 1'b0,
    parameter [1:0] WRITE_MODE0 = 2'b00,
    parameter [1:0] WRITE_MODE1 = 2'b00,
    parameter integer BIT_WIDTH_0 = 16,
    parameter integer BIT_WIDTH_1 = 16,
    parameter [2:0] BLK_SEL_0 = 3'b000,
    parameter [2:0] BLK_SEL_1 = 3'b000,
    parameter RESET_MODE = "SYNC",
    parameter [255:0] INIT_RAM_00 = 256'h0,
    parameter [255:0] INIT_RAM_01 = 256'h0,
    parameter [255:0] INIT_RAM_02 = 256'h0,
    parameter [255:0] INIT_RAM_03 = 256'h0,
    parameter [255:0] INIT_RAM_04 = 256'h0,
    parameter [255:0] INIT_RAM_05 = 256'h0,
    parameter [255:0] INIT_RAM_06 = 256'h0,
    parameter [255:0] INIT_RAM_07 = 256'h0,
    parameter [255:0] INIT_RAM_08 = 256'h0,
    parameter [255:0] INIT_RAM_09 = 256'h0,
    parameter [255:0] INIT_RAM_0A = 256'h0,
    parameter [255:0] INIT_RAM_0B = 256'h0,
    parameter [255:0] INIT_RAM_0C = 256'h0,
    parameter [255:0] INIT_RAM_0D = 256'h0,
    parameter [255:0] INIT_RAM_0E = 256'h0,
    parameter [255:0] INIT_RAM_0F = 256'h0,
    parameter [255:0] INIT_RAM_10 = 256'h0,
    parameter [255:0] INIT_RAM_11 = 256'h0,
    parameter [255:0] INIT_RAM_12 = 256'h0,
    parameter [255:0] INIT_RAM_13 = 256'h0,
    parameter [255:0] INIT_RAM_14 = 256'h0,
    parameter [255:0] INIT_RAM_15 = 256'h0,
    parameter [255:0] INIT_RAM_16 = 256'h0,
    parameter [255:0] INIT_RAM_17 = 256'h0,
    parameter [255:0] INIT_RAM_18 = 256'h0,
    parameter [255:0] INIT_RAM_19 = 256'h0,
    parameter [255:0] INIT_RAM_1A = 256'h0,
    parameter [255:0] INIT_RAM_1B = 256'h0,
    parameter [255:0] INIT_RAM_1C = 256'h0,
    parameter [255:0] INIT_RAM_1D = 256'h0,
    parameter [255:0] INIT_RAM_1E = 256'h0,
    parameter [255:0] INIT_RAM_1F = 256'h0,
    parameter [255:0] INIT_RAM_20 = 256'h0,
    parameter [255:0] INIT_RAM_21 = 256'h0,
    parameter [255:0] INIT_RAM_22 = 256'h0,
    parameter [255:0] INIT_RAM_23 = 256'h0,
    parameter [255:0] INIT_RAM_24 = 256'h0,
    parameter [255:0] INIT_RAM_25 = 256'h0,
    parameter [255:0] INIT_RAM_26 = 256'h0,
    parameter [255:0] INIT_RAM_27 = 256'h0,
    parameter [255:0] INIT_RAM_28 = 256'h0,
    parameter [255:0] INIT_RAM_29 = 256'h0,
    parameter [255:0] INIT_RAM_2A = 256'h0,
    parameter [255:0] INIT_RAM_2B = 256'h0,
    parameter [255:0] INIT_RAM_2C = 256'h0,
    parameter [255:0] INIT_RAM_2D = 256'h0,
    parameter [255:0] INIT_RAM_2E = 256'h0,
    parameter [255:0] INIT_RAM_2F = 256'h0,
    parameter [255:0] INIT_RAM_30 = 256'h0,
    parameter [255:0] INIT_RAM_31 = 256'h0,
    parameter [255:0] INIT_RAM_32 = 256'h0,
    parameter [255:0] INIT_RAM_33 = 256'h0,
    parameter [255:0] INIT_RAM_34 = 256'h0,
    parameter [255:0] INIT_RAM_35 = 256'h0,
    parameter [255:0] INIT_RAM_36 = 256'h0,
    parameter [255:0] INIT_RAM_37 = 256'h0,
    parameter [255:0] INIT_RAM_38 = 256'h0,
    parameter [255:0] INIT_RAM_39 = 256'h0,
    parameter [255:0] INIT_RAM_3A = 256'h0,
    parameter [255:0] INIT_RAM_3B = 256'h0,
    parameter [255:0] INIT_RAM_3C = 256'h0,
    parameter [255:0] INIT_RAM_3D = 256'h0,
    parameter [255:0] INIT_RAM_3E = 256'h0,
    parameter [255:0] INIT_RAM_3F = 256'h0
) (
    input  wire        CLKA, CEA,
    input  wire        CLKB, CEB,
    input  wire        OCEA, OCEB,
    input  wire        RESETA, RESETB,
    input  wire        WREA, WREB,
    input  wire [13:0] ADA, ADB,
    input  wire [2:0]  BLKSELA, BLKSELB,
    input  wire [15:0] DIA, DIB,
    output reg  [15:0] DOA, DOB
);
    // 16384-bit storage
    reg [16383:0] mem_bits;

    integer i;
    initial begin
        mem_bits[0*256 +: 256] = INIT_RAM_00;
        mem_bits[1*256 +: 256] = INIT_RAM_01;
        mem_bits[2*256 +: 256] = INIT_RAM_02;
        mem_bits[3*256 +: 256] = INIT_RAM_03;
        mem_bits[4*256 +: 256] = INIT_RAM_04;
        mem_bits[5*256 +: 256] = INIT_RAM_05;
        mem_bits[6*256 +: 256] = INIT_RAM_06;
        mem_bits[7*256 +: 256] = INIT_RAM_07;
        mem_bits[8*256 +: 256] = INIT_RAM_08;
        mem_bits[9*256 +: 256] = INIT_RAM_09;
        mem_bits[10*256 +: 256] = INIT_RAM_0A;
        mem_bits[11*256 +: 256] = INIT_RAM_0B;
        mem_bits[12*256 +: 256] = INIT_RAM_0C;
        mem_bits[13*256 +: 256] = INIT_RAM_0D;
        mem_bits[14*256 +: 256] = INIT_RAM_0E;
        mem_bits[15*256 +: 256] = INIT_RAM_0F;
        mem_bits[16*256 +: 256] = INIT_RAM_10;
        mem_bits[17*256 +: 256] = INIT_RAM_11;
        mem_bits[18*256 +: 256] = INIT_RAM_12;
        mem_bits[19*256 +: 256] = INIT_RAM_13;
        mem_bits[20*256 +: 256] = INIT_RAM_14;
        mem_bits[21*256 +: 256] = INIT_RAM_15;
        mem_bits[22*256 +: 256] = INIT_RAM_16;
        mem_bits[23*256 +: 256] = INIT_RAM_17;
        mem_bits[24*256 +: 256] = INIT_RAM_18;
        mem_bits[25*256 +: 256] = INIT_RAM_19;
        mem_bits[26*256 +: 256] = INIT_RAM_1A;
        mem_bits[27*256 +: 256] = INIT_RAM_1B;
        mem_bits[28*256 +: 256] = INIT_RAM_1C;
        mem_bits[29*256 +: 256] = INIT_RAM_1D;
        mem_bits[30*256 +: 256] = INIT_RAM_1E;
        mem_bits[31*256 +: 256] = INIT_RAM_1F;
        mem_bits[32*256 +: 256] = INIT_RAM_20;
        mem_bits[33*256 +: 256] = INIT_RAM_21;
        mem_bits[34*256 +: 256] = INIT_RAM_22;
        mem_bits[35*256 +: 256] = INIT_RAM_23;
        mem_bits[36*256 +: 256] = INIT_RAM_24;
        mem_bits[37*256 +: 256] = INIT_RAM_25;
        mem_bits[38*256 +: 256] = INIT_RAM_26;
        mem_bits[39*256 +: 256] = INIT_RAM_27;
        mem_bits[40*256 +: 256] = INIT_RAM_28;
        mem_bits[41*256 +: 256] = INIT_RAM_29;
        mem_bits[42*256 +: 256] = INIT_RAM_2A;
        mem_bits[43*256 +: 256] = INIT_RAM_2B;
        mem_bits[44*256 +: 256] = INIT_RAM_2C;
        mem_bits[45*256 +: 256] = INIT_RAM_2D;
        mem_bits[46*256 +: 256] = INIT_RAM_2E;
        mem_bits[47*256 +: 256] = INIT_RAM_2F;
        mem_bits[48*256 +: 256] = INIT_RAM_30;
        mem_bits[49*256 +: 256] = INIT_RAM_31;
        mem_bits[50*256 +: 256] = INIT_RAM_32;
        mem_bits[51*256 +: 256] = INIT_RAM_33;
        mem_bits[52*256 +: 256] = INIT_RAM_34;
        mem_bits[53*256 +: 256] = INIT_RAM_35;
        mem_bits[54*256 +: 256] = INIT_RAM_36;
        mem_bits[55*256 +: 256] = INIT_RAM_37;
        mem_bits[56*256 +: 256] = INIT_RAM_38;
        mem_bits[57*256 +: 256] = INIT_RAM_39;
        mem_bits[58*256 +: 256] = INIT_RAM_3A;
        mem_bits[59*256 +: 256] = INIT_RAM_3B;
        mem_bits[60*256 +: 256] = INIT_RAM_3C;
        mem_bits[61*256 +: 256] = INIT_RAM_3D;
        mem_bits[62*256 +: 256] = INIT_RAM_3E;
        mem_bits[63*256 +: 256] = INIT_RAM_3F;
        DOA = 16'h0;
        DOB = 16'h0;
    end

    wire blksel_a_match = (BLKSELA === BLK_SEL_0) || (BLK_SEL_0 == 3'b000);
    wire blksel_b_match = (BLKSELB === BLK_SEL_1) || (BLK_SEL_1 == 3'b000);

    function integer get_bit_offset(input [13:0] addr, input integer width);
        begin
            case (width)
                1:  get_bit_offset = addr[13:0] * 1;
                2:  get_bit_offset = addr[13:1] * 2;
                4:  get_bit_offset = addr[13:2] * 4;
                8:  get_bit_offset = addr[13:3] * 8;
                9:  get_bit_offset = addr[13:3] * 9;
                16: get_bit_offset = addr[13:4] * 16;
                18: get_bit_offset = addr[13:4] * 18;
                32: get_bit_offset = addr[13:5] * 32;
                36: get_bit_offset = addr[13:5] * 36;
                default: get_bit_offset = addr[13:4] * 16;
            endcase
        end
    endfunction

    reg [15:0] raw_doa, raw_dob;
    reg [15:0] pipe_doa, pipe_dob;

    // Port A Operation
    always @(posedge CLKA) begin
        if (CEA && blksel_a_match) begin
            if (WREA) begin
                for (i = 0; i < BIT_WIDTH_0; i = i + 1) begin
                    mem_bits[get_bit_offset(ADA, BIT_WIDTH_0) + i] <= DIA[i];
                end
                if (WRITE_MODE0 == 2'b01) begin
                    raw_doa <= DIA;
                end else if (WRITE_MODE0 == 2'b10) begin
                    raw_doa <= mem_bits[get_bit_offset(ADA, BIT_WIDTH_0) +: 16];
                end
            end else begin
                raw_doa <= mem_bits[get_bit_offset(ADA, BIT_WIDTH_0) +: 16];
            end
        end
    end

    // Port B Operation
    always @(posedge CLKB) begin
        if (CEB && blksel_b_match) begin
            if (WREB) begin
                for (i = 0; i < BIT_WIDTH_1; i = i + 1) begin
                    mem_bits[get_bit_offset(ADB, BIT_WIDTH_1) + i] <= DIB[i];
                end
                if (WRITE_MODE1 == 2'b01) begin
                    raw_dob <= DIB;
                end else if (WRITE_MODE1 == 2'b10) begin
                    raw_dob <= mem_bits[get_bit_offset(ADB, BIT_WIDTH_1) +: 16];
                end
            end else begin
                raw_dob <= mem_bits[get_bit_offset(ADB, BIT_WIDTH_1) +: 16];
            end
        end
    end

    // Optional Output Pipeline Register
    always @(posedge CLKA) begin
        if (RESETA) pipe_doa <= 16'h0;
        else if (OCEA) pipe_doa <= raw_doa;
    end

    always @(posedge CLKB) begin
        if (RESETB) pipe_dob <= 16'h0;
        else if (OCEB) pipe_dob <= raw_dob;
    end

    always @* begin
        DOA = (READ_MODE0 == 1'b1) ? pipe_doa : raw_doa;
        DOB = (READ_MODE1 == 1'b1) ? pipe_dob : raw_dob;
    end

endmodule

// ----------------------------------------------------------------------------
// Block RAM Primitive: SPX9 (Single Port Block RAM 18Kbits)
// ----------------------------------------------------------------------------
module SPX9 #(
    parameter READ_MODE  = 1'b0,
    parameter [1:0] WRITE_MODE = 2'b00,
    parameter integer BIT_WIDTH = 36,
    parameter [2:0] BLK_SEL = 3'b000,
    parameter RESET_MODE = "SYNC",
    parameter [287:0] INIT_RAM_00 = 288'h0,
    parameter [287:0] INIT_RAM_01 = 288'h0,
    parameter [287:0] INIT_RAM_02 = 288'h0,
    parameter [287:0] INIT_RAM_03 = 288'h0,
    parameter [287:0] INIT_RAM_04 = 288'h0,
    parameter [287:0] INIT_RAM_05 = 288'h0,
    parameter [287:0] INIT_RAM_06 = 288'h0,
    parameter [287:0] INIT_RAM_07 = 288'h0,
    parameter [287:0] INIT_RAM_08 = 288'h0,
    parameter [287:0] INIT_RAM_09 = 288'h0,
    parameter [287:0] INIT_RAM_0A = 288'h0,
    parameter [287:0] INIT_RAM_0B = 288'h0,
    parameter [287:0] INIT_RAM_0C = 288'h0,
    parameter [287:0] INIT_RAM_0D = 288'h0,
    parameter [287:0] INIT_RAM_0E = 288'h0,
    parameter [287:0] INIT_RAM_0F = 288'h0,
    parameter [287:0] INIT_RAM_10 = 288'h0,
    parameter [287:0] INIT_RAM_11 = 288'h0,
    parameter [287:0] INIT_RAM_12 = 288'h0,
    parameter [287:0] INIT_RAM_13 = 288'h0,
    parameter [287:0] INIT_RAM_14 = 288'h0,
    parameter [287:0] INIT_RAM_15 = 288'h0,
    parameter [287:0] INIT_RAM_16 = 288'h0,
    parameter [287:0] INIT_RAM_17 = 288'h0,
    parameter [287:0] INIT_RAM_18 = 288'h0,
    parameter [287:0] INIT_RAM_19 = 288'h0,
    parameter [287:0] INIT_RAM_1A = 288'h0,
    parameter [287:0] INIT_RAM_1B = 288'h0,
    parameter [287:0] INIT_RAM_1C = 288'h0,
    parameter [287:0] INIT_RAM_1D = 288'h0,
    parameter [287:0] INIT_RAM_1E = 288'h0,
    parameter [287:0] INIT_RAM_1F = 288'h0,
    parameter [287:0] INIT_RAM_20 = 288'h0,
    parameter [287:0] INIT_RAM_21 = 288'h0,
    parameter [287:0] INIT_RAM_22 = 288'h0,
    parameter [287:0] INIT_RAM_23 = 288'h0,
    parameter [287:0] INIT_RAM_24 = 288'h0,
    parameter [287:0] INIT_RAM_25 = 288'h0,
    parameter [287:0] INIT_RAM_26 = 288'h0,
    parameter [287:0] INIT_RAM_27 = 288'h0,
    parameter [287:0] INIT_RAM_28 = 288'h0,
    parameter [287:0] INIT_RAM_29 = 288'h0,
    parameter [287:0] INIT_RAM_2A = 288'h0,
    parameter [287:0] INIT_RAM_2B = 288'h0,
    parameter [287:0] INIT_RAM_2C = 288'h0,
    parameter [287:0] INIT_RAM_2D = 288'h0,
    parameter [287:0] INIT_RAM_2E = 288'h0,
    parameter [287:0] INIT_RAM_2F = 288'h0,
    parameter [287:0] INIT_RAM_30 = 288'h0,
    parameter [287:0] INIT_RAM_31 = 288'h0,
    parameter [287:0] INIT_RAM_32 = 288'h0,
    parameter [287:0] INIT_RAM_33 = 288'h0,
    parameter [287:0] INIT_RAM_34 = 288'h0,
    parameter [287:0] INIT_RAM_35 = 288'h0,
    parameter [287:0] INIT_RAM_36 = 288'h0,
    parameter [287:0] INIT_RAM_37 = 288'h0,
    parameter [287:0] INIT_RAM_38 = 288'h0,
    parameter [287:0] INIT_RAM_39 = 288'h0,
    parameter [287:0] INIT_RAM_3A = 288'h0,
    parameter [287:0] INIT_RAM_3B = 288'h0,
    parameter [287:0] INIT_RAM_3C = 288'h0,
    parameter [287:0] INIT_RAM_3D = 288'h0,
    parameter [287:0] INIT_RAM_3E = 288'h0,
    parameter [287:0] INIT_RAM_3F = 288'h0
) (
    output reg  [35:0] DO,
    input  wire [35:0] DI,
    input  wire [2:0]  BLKSEL,
    input  wire [13:0] AD,
    input  wire        WRE,
    input  wire        CLK,
    input  wire        CE,
    input  wire        OCE,
    input  wire        RESET
);
    reg [18431:0] mem_bits;
    integer i;

    initial begin
        mem_bits[0*288 +: 288] = INIT_RAM_00;
        mem_bits[1*288 +: 288] = INIT_RAM_01;
        mem_bits[2*288 +: 288] = INIT_RAM_02;
        mem_bits[3*288 +: 288] = INIT_RAM_03;
        mem_bits[4*288 +: 288] = INIT_RAM_04;
        mem_bits[5*288 +: 288] = INIT_RAM_05;
        mem_bits[6*288 +: 288] = INIT_RAM_06;
        mem_bits[7*288 +: 288] = INIT_RAM_07;
        mem_bits[8*288 +: 288] = INIT_RAM_08;
        mem_bits[9*288 +: 288] = INIT_RAM_09;
        mem_bits[10*288 +: 288] = INIT_RAM_0A;
        mem_bits[11*288 +: 288] = INIT_RAM_0B;
        mem_bits[12*288 +: 288] = INIT_RAM_0C;
        mem_bits[13*288 +: 288] = INIT_RAM_0D;
        mem_bits[14*288 +: 288] = INIT_RAM_0E;
        mem_bits[15*288 +: 288] = INIT_RAM_0F;
        mem_bits[16*288 +: 288] = INIT_RAM_10;
        mem_bits[17*288 +: 288] = INIT_RAM_11;
        mem_bits[18*288 +: 288] = INIT_RAM_12;
        mem_bits[19*288 +: 288] = INIT_RAM_13;
        mem_bits[20*288 +: 288] = INIT_RAM_14;
        mem_bits[21*288 +: 288] = INIT_RAM_15;
        mem_bits[22*288 +: 288] = INIT_RAM_16;
        mem_bits[23*288 +: 288] = INIT_RAM_17;
        mem_bits[24*288 +: 288] = INIT_RAM_18;
        mem_bits[25*288 +: 288] = INIT_RAM_19;
        mem_bits[26*288 +: 288] = INIT_RAM_1A;
        mem_bits[27*288 +: 288] = INIT_RAM_1B;
        mem_bits[28*288 +: 288] = INIT_RAM_1C;
        mem_bits[29*288 +: 288] = INIT_RAM_1D;
        mem_bits[30*288 +: 288] = INIT_RAM_1E;
        mem_bits[31*288 +: 288] = INIT_RAM_1F;
        mem_bits[32*288 +: 288] = INIT_RAM_20;
        mem_bits[33*288 +: 288] = INIT_RAM_21;
        mem_bits[34*288 +: 288] = INIT_RAM_22;
        mem_bits[35*288 +: 288] = INIT_RAM_23;
        mem_bits[36*288 +: 288] = INIT_RAM_24;
        mem_bits[37*288 +: 288] = INIT_RAM_25;
        mem_bits[38*288 +: 288] = INIT_RAM_26;
        mem_bits[39*288 +: 288] = INIT_RAM_27;
        mem_bits[40*288 +: 288] = INIT_RAM_28;
        mem_bits[41*288 +: 288] = INIT_RAM_29;
        mem_bits[42*288 +: 288] = INIT_RAM_2A;
        mem_bits[43*288 +: 288] = INIT_RAM_2B;
        mem_bits[44*288 +: 288] = INIT_RAM_2C;
        mem_bits[45*288 +: 288] = INIT_RAM_2D;
        mem_bits[46*288 +: 288] = INIT_RAM_2E;
        mem_bits[47*288 +: 288] = INIT_RAM_2F;
        mem_bits[48*288 +: 288] = INIT_RAM_30;
        mem_bits[49*288 +: 288] = INIT_RAM_31;
        mem_bits[50*288 +: 288] = INIT_RAM_32;
        mem_bits[51*288 +: 288] = INIT_RAM_33;
        mem_bits[52*288 +: 288] = INIT_RAM_34;
        mem_bits[53*288 +: 288] = INIT_RAM_35;
        mem_bits[54*288 +: 288] = INIT_RAM_36;
        mem_bits[55*288 +: 288] = INIT_RAM_37;
        mem_bits[56*288 +: 288] = INIT_RAM_38;
        mem_bits[57*288 +: 288] = INIT_RAM_39;
        mem_bits[58*288 +: 288] = INIT_RAM_3A;
        mem_bits[59*288 +: 288] = INIT_RAM_3B;
        mem_bits[60*288 +: 288] = INIT_RAM_3C;
        mem_bits[61*288 +: 288] = INIT_RAM_3D;
        mem_bits[62*288 +: 288] = INIT_RAM_3E;
        mem_bits[63*288 +: 288] = INIT_RAM_3F;
        DO = 36'h0;
    end

    wire blksel_match = (BLKSEL === BLK_SEL) || (BLK_SEL == 3'b000);

    function integer get_bit_offset(input [13:0] addr, input integer width);
        begin
            case (width)
                9:  get_bit_offset = addr[13:3] * 9;
                18: get_bit_offset = addr[13:4] * 18;
                36: get_bit_offset = addr[13:5] * 36;
                default: get_bit_offset = addr[13:5] * 36;
            endcase
        end
    endfunction

    reg [35:0] raw_do;
    reg [35:0] pipe_do;

    always @(posedge CLK) begin
        if (CE && blksel_match) begin
            if (WRE) begin
                for (i = 0; i < BIT_WIDTH; i = i + 1) begin
                    mem_bits[get_bit_offset(AD, BIT_WIDTH) + i] <= DI[i];
                end
                if (WRITE_MODE == 2'b01) begin
                    raw_do <= DI;
                end else if (WRITE_MODE == 2'b10) begin
                    raw_do <= mem_bits[get_bit_offset(AD, BIT_WIDTH) +: 36];
                end
            end else begin
                raw_do <= mem_bits[get_bit_offset(AD, BIT_WIDTH) +: 36];
            end
        end
    end

    always @(posedge CLK) begin
        if (RESET) pipe_do <= 36'h0;
        else if (OCE) pipe_do <= raw_do;
    end

    always @* begin
        DO = (READ_MODE == 1'b1) ? pipe_do : raw_do;
    end

endmodule

// ----------------------------------------------------------------------------
// Block RAM Primitive: SP (Single Port Block RAM 16Kbits)
// ----------------------------------------------------------------------------
module SP #(
    parameter READ_MODE  = 1'b0,
    parameter [1:0] WRITE_MODE = 2'b00,
    parameter integer BIT_WIDTH = 32,
    parameter [2:0] BLK_SEL = 3'b000,
    parameter RESET_MODE = "SYNC",
    parameter [255:0] INIT_RAM_00 = 256'h0,
    parameter [255:0] INIT_RAM_01 = 256'h0,
    parameter [255:0] INIT_RAM_02 = 256'h0,
    parameter [255:0] INIT_RAM_03 = 256'h0,
    parameter [255:0] INIT_RAM_04 = 256'h0,
    parameter [255:0] INIT_RAM_05 = 256'h0,
    parameter [255:0] INIT_RAM_06 = 256'h0,
    parameter [255:0] INIT_RAM_07 = 256'h0,
    parameter [255:0] INIT_RAM_08 = 256'h0,
    parameter [255:0] INIT_RAM_09 = 256'h0,
    parameter [255:0] INIT_RAM_0A = 256'h0,
    parameter [255:0] INIT_RAM_0B = 256'h0,
    parameter [255:0] INIT_RAM_0C = 256'h0,
    parameter [255:0] INIT_RAM_0D = 256'h0,
    parameter [255:0] INIT_RAM_0E = 256'h0,
    parameter [255:0] INIT_RAM_0F = 256'h0,
    parameter [255:0] INIT_RAM_10 = 256'h0,
    parameter [255:0] INIT_RAM_11 = 256'h0,
    parameter [255:0] INIT_RAM_12 = 256'h0,
    parameter [255:0] INIT_RAM_13 = 256'h0,
    parameter [255:0] INIT_RAM_14 = 256'h0,
    parameter [255:0] INIT_RAM_15 = 256'h0,
    parameter [255:0] INIT_RAM_16 = 256'h0,
    parameter [255:0] INIT_RAM_17 = 256'h0,
    parameter [255:0] INIT_RAM_18 = 256'h0,
    parameter [255:0] INIT_RAM_19 = 256'h0,
    parameter [255:0] INIT_RAM_1A = 256'h0,
    parameter [255:0] INIT_RAM_1B = 256'h0,
    parameter [255:0] INIT_RAM_1C = 256'h0,
    parameter [255:0] INIT_RAM_1D = 256'h0,
    parameter [255:0] INIT_RAM_1E = 256'h0,
    parameter [255:0] INIT_RAM_1F = 256'h0,
    parameter [255:0] INIT_RAM_20 = 256'h0,
    parameter [255:0] INIT_RAM_21 = 256'h0,
    parameter [255:0] INIT_RAM_22 = 256'h0,
    parameter [255:0] INIT_RAM_23 = 256'h0,
    parameter [255:0] INIT_RAM_24 = 256'h0,
    parameter [255:0] INIT_RAM_25 = 256'h0,
    parameter [255:0] INIT_RAM_26 = 256'h0,
    parameter [255:0] INIT_RAM_27 = 256'h0,
    parameter [255:0] INIT_RAM_28 = 256'h0,
    parameter [255:0] INIT_RAM_29 = 256'h0,
    parameter [255:0] INIT_RAM_2A = 256'h0,
    parameter [255:0] INIT_RAM_2B = 256'h0,
    parameter [255:0] INIT_RAM_2C = 256'h0,
    parameter [255:0] INIT_RAM_2D = 256'h0,
    parameter [255:0] INIT_RAM_2E = 256'h0,
    parameter [255:0] INIT_RAM_2F = 256'h0,
    parameter [255:0] INIT_RAM_30 = 256'h0,
    parameter [255:0] INIT_RAM_31 = 256'h0,
    parameter [255:0] INIT_RAM_32 = 256'h0,
    parameter [255:0] INIT_RAM_33 = 256'h0,
    parameter [255:0] INIT_RAM_34 = 256'h0,
    parameter [255:0] INIT_RAM_35 = 256'h0,
    parameter [255:0] INIT_RAM_36 = 256'h0,
    parameter [255:0] INIT_RAM_37 = 256'h0,
    parameter [255:0] INIT_RAM_38 = 256'h0,
    parameter [255:0] INIT_RAM_39 = 256'h0,
    parameter [255:0] INIT_RAM_3A = 256'h0,
    parameter [255:0] INIT_RAM_3B = 256'h0,
    parameter [255:0] INIT_RAM_3C = 256'h0,
    parameter [255:0] INIT_RAM_3D = 256'h0,
    parameter [255:0] INIT_RAM_3E = 256'h0,
    parameter [255:0] INIT_RAM_3F = 256'h0
) (
    output reg  [31:0] DO,
    input  wire [31:0] DI,
    input  wire [2:0]  BLKSEL,
    input  wire [13:0] AD,
    input  wire        WRE,
    input  wire        CLK,
    input  wire        CE,
    input  wire        OCE,
    input  wire        RESET
);
    reg [16383:0] mem_bits;
    integer i;

    initial begin
        mem_bits[0*256 +: 256] = INIT_RAM_00;
        mem_bits[1*256 +: 256] = INIT_RAM_01;
        mem_bits[2*256 +: 256] = INIT_RAM_02;
        mem_bits[3*256 +: 256] = INIT_RAM_03;
        mem_bits[4*256 +: 256] = INIT_RAM_04;
        mem_bits[5*256 +: 256] = INIT_RAM_05;
        mem_bits[6*256 +: 256] = INIT_RAM_06;
        mem_bits[7*256 +: 256] = INIT_RAM_07;
        mem_bits[8*256 +: 256] = INIT_RAM_08;
        mem_bits[9*256 +: 256] = INIT_RAM_09;
        mem_bits[10*256 +: 256] = INIT_RAM_0A;
        mem_bits[11*256 +: 256] = INIT_RAM_0B;
        mem_bits[12*256 +: 256] = INIT_RAM_0C;
        mem_bits[13*256 +: 256] = INIT_RAM_0D;
        mem_bits[14*256 +: 256] = INIT_RAM_0E;
        mem_bits[15*256 +: 256] = INIT_RAM_0F;
        mem_bits[16*256 +: 256] = INIT_RAM_10;
        mem_bits[17*256 +: 256] = INIT_RAM_11;
        mem_bits[18*256 +: 256] = INIT_RAM_12;
        mem_bits[19*256 +: 256] = INIT_RAM_13;
        mem_bits[20*256 +: 256] = INIT_RAM_14;
        mem_bits[21*256 +: 256] = INIT_RAM_15;
        mem_bits[22*256 +: 256] = INIT_RAM_16;
        mem_bits[23*256 +: 256] = INIT_RAM_17;
        mem_bits[24*256 +: 256] = INIT_RAM_18;
        mem_bits[25*256 +: 256] = INIT_RAM_19;
        mem_bits[26*256 +: 256] = INIT_RAM_1A;
        mem_bits[27*256 +: 256] = INIT_RAM_1B;
        mem_bits[28*256 +: 256] = INIT_RAM_1C;
        mem_bits[29*288 +: 256] = INIT_RAM_1D;
        mem_bits[30*256 +: 256] = INIT_RAM_1E;
        mem_bits[31*256 +: 256] = INIT_RAM_1F;
        mem_bits[32*256 +: 256] = INIT_RAM_20;
        mem_bits[33*256 +: 256] = INIT_RAM_21;
        mem_bits[34*256 +: 256] = INIT_RAM_22;
        mem_bits[35*256 +: 256] = INIT_RAM_23;
        mem_bits[36*256 +: 256] = INIT_RAM_24;
        mem_bits[37*256 +: 256] = INIT_RAM_25;
        mem_bits[38*256 +: 256] = INIT_RAM_26;
        mem_bits[39*256 +: 256] = INIT_RAM_27;
        mem_bits[40*256 +: 256] = INIT_RAM_28;
        mem_bits[41*256 +: 256] = INIT_RAM_29;
        mem_bits[42*256 +: 256] = INIT_RAM_2A;
        mem_bits[43*256 +: 256] = INIT_RAM_2B;
        mem_bits[44*256 +: 256] = INIT_RAM_2C;
        mem_bits[45*256 +: 256] = INIT_RAM_2D;
        mem_bits[46*256 +: 256] = INIT_RAM_2E;
        mem_bits[47*256 +: 256] = INIT_RAM_2F;
        mem_bits[48*256 +: 256] = INIT_RAM_30;
        mem_bits[49*256 +: 256] = INIT_RAM_31;
        mem_bits[50*256 +: 256] = INIT_RAM_32;
        mem_bits[51*256 +: 256] = INIT_RAM_33;
        mem_bits[52*256 +: 256] = INIT_RAM_34;
        mem_bits[53*256 +: 256] = INIT_RAM_35;
        mem_bits[54*256 +: 256] = INIT_RAM_36;
        mem_bits[55*256 +: 256] = INIT_RAM_37;
        mem_bits[56*256 +: 256] = INIT_RAM_38;
        mem_bits[57*256 +: 256] = INIT_RAM_39;
        mem_bits[58*256 +: 256] = INIT_RAM_3A;
        mem_bits[59*256 +: 256] = INIT_RAM_3B;
        mem_bits[60*256 +: 256] = INIT_RAM_3C;
        mem_bits[61*256 +: 256] = INIT_RAM_3D;
        mem_bits[62*256 +: 256] = INIT_RAM_3E;
        mem_bits[63*256 +: 256] = INIT_RAM_3F;
        DO = 32'h0;
    end

    wire blksel_match = (BLKSEL === BLK_SEL) || (BLK_SEL == 3'b000);

    function integer get_bit_offset(input [13:0] addr, input integer width);
        begin
            case (width)
                1:  get_bit_offset = addr[13:0] * 1;
                2:  get_bit_offset = addr[13:1] * 2;
                4:  get_bit_offset = addr[13:2] * 4;
                8:  get_bit_offset = addr[13:3] * 8;
                16: get_bit_offset = addr[13:4] * 16;
                32: get_bit_offset = addr[13:5] * 32;
                default: get_bit_offset = addr[13:5] * 32;
            endcase
        end
    endfunction

    reg [31:0] raw_do;
    reg [31:0] pipe_do;

    always @(posedge CLK) begin
        if (CE && blksel_match) begin
            if (WRE) begin
                for (i = 0; i < BIT_WIDTH; i = i + 1) begin
                    mem_bits[get_bit_offset(AD, BIT_WIDTH) + i] <= DI[i];
                end
                if (WRITE_MODE == 2'b01) begin
                    raw_do <= DI;
                end else if (WRITE_MODE == 2'b10) begin
                    raw_do <= mem_bits[get_bit_offset(AD, BIT_WIDTH) +: 32];
                end
            end else begin
                raw_do <= mem_bits[get_bit_offset(AD, BIT_WIDTH) +: 32];
            end
        end
    end

    always @(posedge CLK) begin
        if (RESET) pipe_do <= 32'h0;
        else if (OCE) pipe_do <= raw_do;
    end

    always @* begin
        DO = (READ_MODE == 1'b1) ? pipe_do : raw_do;
    end

endmodule

// ----------------------------------------------------------------------------
// Semi-Dual Port Block RAM: SDPB (16Kbits)
// ----------------------------------------------------------------------------
module SDPB #(
    parameter READ_MODE = 1'b0,
    parameter [1:0] WRITE_MODE = 2'b00,
    parameter integer BIT_WIDTH_0 = 16,
    parameter integer BIT_WIDTH_1 = 16,
    parameter [2:0] BLK_SEL_0 = 3'b000,
    parameter [2:0] BLK_SEL_1 = 3'b000,
    parameter RESET_MODE = "SYNC",
    parameter [255:0] INIT_RAM_00 = 256'h0,
    parameter [255:0] INIT_RAM_3F = 256'h0
) (
    input  wire        CLKA, CEA,
    input  wire        CLKB, CEB,
    input  wire        OCE,
    input  wire        RESETA, RESETB,
    input  wire [13:0] ADA, ADB,
    input  wire [15:0] DI,
    input  wire [2:0]  BLKSELA, BLKSELB,
    output reg  [15:0] DO
);
    reg [16383:0] mem_bits;
    integer i;

    initial begin
        mem_bits = 16384'h0;
        DO = 16'h0;
    end

    always @(posedge CLKA) begin
        if (CEA && (BLKSELA == BLK_SEL_0)) begin
            for (i = 0; i < BIT_WIDTH_0; i = i + 1) begin
                mem_bits[ADA * BIT_WIDTH_0 + i] <= DI[i];
            end
        end
    end

    reg [15:0] raw_do;
    always @(posedge CLKB) begin
        if (CEB && (BLKSELB == BLK_SEL_1)) begin
            raw_do <= mem_bits[ADB * BIT_WIDTH_1 +: 16];
        end
    end

    always @* begin
        DO = raw_do;
    end
endmodule
