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
    output logic       sd_cs_n,
    // Lockstep trace (RTL builds, see below): 0 = off, else the program being traced
    input  logic [15:0] trace_id
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

`ifdef VUX9K_RTL_TRACE
    // Lockstep trace for sim/emu/test_lockstep.py: what the CPU and the SoC did in each
    // clock cycle, written to lockstep.trace in the simulator's cwd once trace_id is
    // set (sim_runner defines VUX9K_RTL_TRACE for RTL builds only: the GLS netlist has
    // none of these names). Cycle 0 is the first cycle out of reset (raw_rst high),
    // the emulator's cycle 0. Records, one per line, numbers in hex:
    //   P id                     trace_id changed: a new program starts
    //   F cycle pc               FETCH (not while cpu_soft_rst holds the CPU)
    //   M cycle isa              active_mode from this cycle on (1 = RV32); it latches
    //                            the auto-detected ISA at the end of a FETCH
    //   R cycle rd data          register file write, x0 excluded (Hack: A = x1, D = x2)
    //   W cycle addr data be     CPU bus write
    //   T cycle cause            trap entry
    //   U cycle level            uart_tx pin change
    integer fd = 0;
    logic [63:0] cyc = 0;
    logic [15:0] traced_id = 0;
    logic last_tx = 1'b1;
    logic last_mode = 1'b0;
    logic mode_known = 1'b0;
    always @(posedge clk) begin
        if (trace_id != 0 && fd == 0) fd = $fopen("lockstep.trace", "w");
        if (fd != 0) begin
            if (trace_id != traced_id) begin
                $fwrite(fd, "P %h\n", trace_id);
                traced_id = trace_id;
            end
            if (soc.raw_rst) begin
                if (!mode_known || soc.active_mode != last_mode)
                    $fwrite(fd, "M %h %h\n", cyc, soc.active_mode);
                mode_known = 1'b1;
                last_mode = soc.active_mode;
                if (int'(soc.cpu_inst.state) == 0 && !soc.cpu_soft_rst)  // CpuState_FETCH
                    $fwrite(fd, "F %h %h\n", cyc, soc.cpu_inst.pc_reg);
                // The register file's own ports: one write per cycle, port 1 first
                if (soc.cpu_inst.reg_file.we && soc.cpu_inst.reg_file.rd_addr != 0)
                    $fwrite(fd, "R %h %h %h\n", cyc, soc.cpu_inst.reg_file.rd_addr, soc.cpu_inst.reg_file.wr_data);
                else if (soc.cpu_inst.reg_file.we2 && soc.cpu_inst.reg_file.rd2_addr != 0)
                    $fwrite(fd, "R %h %h %h\n", cyc, soc.cpu_inst.reg_file.rd2_addr, soc.cpu_inst.reg_file.wr2_data);
                if (soc.mem_write)
                    $fwrite(fd, "W %h %h %h %h\n", cyc, soc.data_waddr, soc.cpu_data_out, soc.cpu_inst.mem_byte_we);
                if (soc.cpu_inst.trap_entry)
                    $fwrite(fd, "T %h %h\n", cyc, soc.cpu_inst.trap_cause);
                if (uart_tx != last_tx)
                    $fwrite(fd, "U %h %h\n", cyc, uart_tx);
                cyc = cyc + 1;
            end else begin
                cyc = 0;
                mode_known = 1'b0;
            end
            last_tx = uart_tx;
        end
    end
`endif
endmodule
