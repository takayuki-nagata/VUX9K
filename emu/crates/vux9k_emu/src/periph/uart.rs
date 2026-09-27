// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! UART, soc/uart/uart_controller.veryl + uart_tx/uart_rx/fifo_sync/clk_timer, as
//! soc_top wires it: 115200 8N1 at 27 MHz (234 clocks per bit), 32-byte TX and RX
//! FIFOs, TX only from register 0x0, status {frame_err, overrun, tx_full, rx_empty}
//! whose error bits are cleared by reading the status register, and the machine
//! external interrupt while the RX FIFO holds data.
//!
//! Timing (all derived from the RTL, and proven against it by the lockstep tests):
//! - The TX bit timer free-runs from reset: its alarm fires in cycles 233 + 234n.
//!   uart_tx leaves RST at the first alarm; a byte popped from the FIFO while it is
//!   idle is loaded at the next alarm, its start bit begins the cycle after, and the
//!   transmitter is idle again 9 bit times + 2 cycles after the load.
//! - A byte whose start bit begins in cycle s is pushed into the RX FIFO at the end of
//!   cycle s + 2226 (the middle of its stop bit), or dropped with `overrun` if the
//!   FIFO is full then.
//! - The CPU reads the data/status in its MEM_WAIT cycle (combinational); the pop and
//!   the flag clear happen at the end of that cycle.
//!
//! Everything that happens at one clock edge is decided from the state during that
//! cycle; CPU effects are therefore queued and applied at their edge, after the UART's
//! own events have been evaluated.

use std::collections::VecDeque;

/// Clocks per bit: soc_pkg::UART_CNT = 27_000_000 / 115_200.
pub const BIT: u64 = 234;
/// Clocks per 8N1 frame (start, 8 data, stop).
pub const FRAME: u64 = 10 * BIT;
pub const FIFO_DEPTH: usize = 32;
/// First TX bit-timer alarm (clk_timer counts down from CNT - 1 after reset).
const FIRST_ALARM: u64 = BIT - 1;
/// Start of the start bit -> RX FIFO push edge (see the module docs).
const RX_PUSH_DELAY: u64 = 2226;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CpuOp {
    TxPush(u8),
    RxPop,
    ClearErrors,
}

/// A byte on the RX line: its value, the cycle its start bit begins, and whether its
/// stop bit is low (a framing error).
#[derive(Clone, Copy, Debug)]
struct RxFrame {
    byte: u8,
    start: u64,
    bad_stop: bool,
}

#[derive(Clone, Debug)]
pub struct Uart {
    /// Cycle whose state the UART holds: every edge before it has been applied.
    now: u64,
    tx_fifo: VecDeque<u8>,
    /// First cycle in which uart_tx is IDLE again.
    tx_idle_from: u64,
    /// Transmitted bytes: (byte, cycle its start bit begins).
    tx_out: Vec<(u8, u64)>,
    /// The same bytes, alone.
    tx_bytes: Vec<u8>,
    rx_fifo: VecDeque<u8>,
    rx_line: VecDeque<RxFrame>,
    /// Earliest start of the next byte the host may send (back-to-back frames).
    rx_line_free: u64,
    overrun: bool,
    frame_err: bool,
    /// CPU effects waiting for their edge: (edge cycle, op), in time order.
    cpu_ops: VecDeque<(u64, CpuOp)>,
}

impl Default for Uart {
    fn default() -> Self {
        Uart {
            now: 0,
            tx_fifo: VecDeque::new(),
            tx_idle_from: FIRST_ALARM + 1,
            tx_out: Vec::new(),
            tx_bytes: Vec::new(),
            rx_fifo: VecDeque::new(),
            rx_line: VecDeque::new(),
            rx_line_free: 0,
            overrun: false,
            frame_err: false,
            cpu_ops: VecDeque::new(),
        }
    }
}

/// First TX bit-timer alarm in cycle `c` or later.
fn next_alarm(c: u64) -> u64 {
    if c <= FIRST_ALARM {
        FIRST_ALARM
    } else {
        FIRST_ALARM + (c - FIRST_ALARM).div_ceil(BIT) * BIT
    }
}

impl Uart {
    /// Apply every edge before cycle `to`, so the state is the one during `to`.
    pub fn advance(&mut self, to: u64) {
        while self.now < to {
            // Next edge at which something happens (all < `to` or we stop)
            let tx_pop = (!self.tx_fifo.is_empty()).then(|| self.now.max(self.tx_idle_from));
            let rx_push = self.rx_line.front().map(|f| f.start + RX_PUSH_DELAY);
            let cpu = self.cpu_ops.front().map(|op| op.0);
            let Some(edge) = [tx_pop, rx_push, cpu].into_iter().flatten().min() else {
                self.now = to;
                return;
            };
            if edge >= to {
                self.now = to;
                return;
            }
            self.apply_edge(edge, tx_pop == Some(edge), rx_push == Some(edge));
            self.now = edge + 1;
        }
    }

    /// Everything that happens at the end of cycle `edge`, decided from the state
    /// during it.
    fn apply_edge(&mut self, edge: u64, tx_pop: bool, rx_push: bool) {
        let rx_full = self.rx_fifo.len() == FIFO_DEPTH;
        // A flag being set wins over a clear in the same cycle (per flag)
        let (mut overrun_set, mut frame_err_set) = (false, false);
        if tx_pop {
            let byte = self.tx_fifo.pop_front().expect("tx_pop only with data");
            let load = next_alarm(edge + 1);
            self.tx_out.push((byte, load + 1));
            self.tx_bytes.push(byte);
            self.tx_idle_from = load + 9 * BIT + 2;
        }
        let mut pushed = None;
        if rx_push {
            let f = self.rx_line.pop_front().expect("rx_push only with a frame");
            // uart_rx raises rdy even for a bad stop bit: the byte is kept
            if rx_full {
                self.overrun = true;
                overrun_set = true;
            } else {
                pushed = Some(f.byte);
            }
            if f.bad_stop {
                self.frame_err = true;
                frame_err_set = true;
            }
        }
        while let Some(&(e, op)) = self.cpu_ops.front() {
            if e != edge {
                break;
            }
            self.cpu_ops.pop_front();
            match op {
                CpuOp::TxPush(b) => self.tx_fifo.push_back(b),
                CpuOp::RxPop => {
                    self.rx_fifo.pop_front();
                }
                CpuOp::ClearErrors => {
                    self.overrun &= overrun_set;
                    self.frame_err &= frame_err_set;
                }
            }
        }
        if let Some(b) = pushed {
            self.rx_fifo.push_back(b);
        }
    }

    fn queue(&mut self, edge: u64, op: CpuOp) {
        self.cpu_ops.push_back((edge, op));
    }

    /// Status during cycle `c` (the state must have been advanced to `c`).
    fn status(&self) -> u32 {
        (self.frame_err as u32) << 3
            | (self.overrun as u32) << 2
            | ((self.tx_fifo.len() == FIFO_DEPTH) as u32) << 1
            | self.rx_fifo.is_empty() as u32
    }

    /// CPU read in cycle `c` (its MEM_WAIT cycle): data (offset 0, pops a byte) or
    /// status (any other offset, clears the error bits).
    pub fn read(&mut self, addr: u32, c: u64) -> u32 {
        self.advance(c);
        if addr & 0xF == 0 {
            let head = self.rx_fifo.front().copied();
            if head.is_some() {
                self.queue(c, CpuOp::RxPop);
            }
            head.unwrap_or(0) as u32
        } else {
            let s = self.status();
            self.queue(c, CpuOp::ClearErrors);
            s
        }
    }

    /// CPU write at the end of cycle `c`: only register 0x0 transmits, and only when
    /// the TX FIFO isn't full.
    pub fn write(&mut self, addr: u32, v: u32, c: u64) {
        if addr & 0xF != 0 {
            return;
        }
        self.advance(c);
        if self.tx_fifo.len() < FIFO_DEPTH {
            self.queue(c, CpuOp::TxPush(v as u8));
        }
    }

    /// Bytes in the RX FIFO during cycle `c`.
    pub fn rx_level(&mut self, c: u64) -> usize {
        self.advance(c);
        self.rx_fifo.len()
    }

    /// Sticky overrun flag during cycle `c` (as the status register would read it).
    pub fn overrun(&mut self, c: u64) -> bool {
        self.advance(c);
        self.overrun
    }

    /// True while the RX FIFO holds data during cycle `c` (the MEI line).
    pub fn rx_pending(&mut self, c: u64) -> bool {
        self.advance(c);
        !self.rx_fifo.is_empty()
    }

    /// The host sends `bytes` on the RX line, back to back, starting no earlier than
    /// cycle `c`. `bad_stop` sends them with a low stop bit (framing error).
    pub fn host_send(&mut self, bytes: &[u8], c: u64, bad_stop: bool) {
        for &byte in bytes {
            let start = self.rx_line_free.max(c);
            self.rx_line.push_back(RxFrame {
                byte,
                start,
                bad_stop,
            });
            self.rx_line_free = start + FRAME;
        }
    }

    /// Cycle by which everything sent so far has reached the RX FIFO (or was dropped).
    pub fn host_send_done(&self) -> u64 {
        self.rx_line
            .back()
            .map_or(0, |f| f.start + RX_PUSH_DELAY + 1)
    }

    /// Transmitted bytes, with the cycle their start bit began, in order.
    pub fn tx_log(&self) -> &[(u8, u64)] {
        &self.tx_out
    }

    /// Transmitted bytes (whose start bit has begun), without their times.
    pub fn tx_bytes(&self) -> &[u8] {
        &self.tx_bytes
    }

    /// Number of transmitted bytes whose frame has completely left the pin by cycle
    /// `c` (the state must have been advanced to `c`).
    pub fn tx_complete_by(&self, c: u64) -> usize {
        self.tx_out.partition_point(|&(_, s)| s + FRAME <= c)
    }
}
