// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// Direct Verilog Harvard Memory Module with ROM Read Support
// I-ROM/RAM: 16 KB (4096 x 32-bit words) in DPX9B BSRAMs (Dual Port: Port A = Instruction Fetch, Port B = Data Read)
// D-RAM:     4 KB (1024 x 32-bit words) in SPX9 BSRAMs
module soc_ram #(
    parameter integer I_MEM_WORDS = 4096, // 16 KB
    parameter integer D_MEM_WORDS = 1024  // 4 KB
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        active_mode,
    input  wire [31:0] i_addr,
    output reg  [31:0] i_data_out,
    input  wire        i_we,
    input  wire [11:0] i_waddr,
    input  wire [31:0] i_wdata,
    input  wire [31:0] d_addr,
    input  wire [31:0] d_data_in,
    output reg  [31:0] d_data_out,
    input  wire        d_we,
    input  wire [3:0]  d_we_byte
);

    // 1. Instruction ROM / RAM (True Dual Port BSRAM)
    (* syn_ramstyle = "block_ram" *) reg [31:0] i_mem [0:I_MEM_WORDS-1];
    wire [11:0] i_idx = i_addr[13:2];
    wire [11:0] i_d_idx = d_addr[13:2];
    reg  [31:0] i_dout_b;

    initial begin
        $readmemh("firmware.hex", i_mem);
    end

    // Port A: Instruction Fetch & Boot Loader Write
    always @(posedge clk) begin
        if (i_we) begin
            i_mem[i_waddr] <= i_wdata;
        end
        i_data_out <= i_mem[i_idx];
    end

    // Port B: Data Read from ROM (e.g. rodata string constants, sidata)
    always @(posedge clk) begin
        i_dout_b <= i_mem[i_d_idx];
    end

    // 2. Data Memory (4 Byte-wide Single-Port BSRAMs)
    (* syn_ramstyle = "block_ram" *) reg [7:0] d_mem0 [0:D_MEM_WORDS-1];
    (* syn_ramstyle = "block_ram" *) reg [7:0] d_mem1 [0:D_MEM_WORDS-1];
    (* syn_ramstyle = "block_ram" *) reg [7:0] d_mem2 [0:D_MEM_WORDS-1];
    (* syn_ramstyle = "block_ram" *) reg [7:0] d_mem3 [0:D_MEM_WORDS-1];

    wire [9:0] d_idx = (active_mode == 1'b0) ? d_addr[9:0] : d_addr[11:2];

    wire is_dram_access = (active_mode == 1'b0) || (d_addr[31:20] == 12'h200);

    wire we0 = d_we && is_dram_access && (active_mode ? d_we_byte[0] : 1'b1);
    wire we1 = d_we && is_dram_access && (active_mode ? d_we_byte[1] : 1'b1);
    wire we2 = d_we && is_dram_access && (active_mode ? d_we_byte[2] : 1'b1);
    wire we3 = d_we && is_dram_access && (active_mode ? d_we_byte[3] : 1'b1);

    reg [7:0] d_dout0;
    reg [7:0] d_dout1;
    reg [7:0] d_dout2;
    reg [7:0] d_dout3;

    always @(posedge clk) begin
        if (we0) d_mem0[d_idx] <= d_data_in[7:0];
        if (we1) d_mem1[d_idx] <= d_data_in[15:8];
        if (we2) d_mem2[d_idx] <= d_data_in[23:16];
        if (we3) d_mem3[d_idx] <= d_data_in[31:24];

        d_dout0 <= d_mem0[d_idx];
        d_dout1 <= d_mem1[d_idx];
        d_dout2 <= d_mem2[d_idx];
        d_dout3 <= d_mem3[d_idx];
    end

    reg last_is_rom;
    always @(posedge clk) begin
        last_is_rom <= (d_addr[31:16] == 16'h0000);
    end

    always @(*) begin
        if (last_is_rom) begin
            d_data_out = i_dout_b;
        end else begin
            d_data_out = {d_dout3, d_dout2, d_dout1, d_dout0};
        end
    end

endmodule
