// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// cocotb testbench wrapper for soc_top (RTL or the synthesized GLS netlist).
//
// The only thing it adds is the 27 MHz board clock, generated in HDL. Driving the
// clock from cocotb (cocotb.clock.Clock) costs a VPI write plus a callback on every
// half period, which made Icarus run soc_top ~2.4x slower than with an HDL clock
// (7.9k vs 19k cycles/s, 2026-09). Everything else is passed straight through; the
// SoC itself is reachable from tests as `dut.soc`. See sim/integration/soc_env.py.
//
// This is the one hand-written SystemVerilog file in the repository, on purpose:
// Veryl has no time-based clock outside `#[test]` modules run by its own simulator
// ($tb::clock_gen), so the generated SV of a Veryl wrapper would need the clock from
// cocotb again. Re-measured before keeping it (test_soc_fast, RTL, 2026-09, wall
// time incl. compile): Verilator 8.9 s with this wrapper vs 22.7 s with a cocotb
// Clock on bare soc_top, Icarus 230 s vs 416 s.

`timescale 1ps / 1ps

module tb_soc_top #(
    parameter int HALF_PERIOD_PS = 18519  // 37.038 ns period = 27.0 MHz
) (
    input  logic       rst_n,
    input  logic       btn,
    input  logic       uart_rx,
    output logic       uart_tx,
    output logic [5:0] led,
    output logic       sd_sclk,
    output logic       sd_mosi,
    input  logic       sd_miso,
    output logic       sd_cs_n
);
    logic clk = 1'b0;
    always #(HALF_PERIOD_PS) clk = ~clk;

    soc_top soc (
        .clk    (clk),
        .rst_n  (rst_n),
        .btn    (btn),
        .uart_rx(uart_rx),
        .uart_tx(uart_tx),
        .led    (led),
        .sd_sclk(sd_sclk),
        .sd_mosi(sd_mosi),
        .sd_miso(sd_miso),
        .sd_cs_n(sd_cs_n)
    );
endmodule
