// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! UART, soc/uart/uart_controller.veryl + uart_tx/uart_rx/fifo_sync/clk_timer behind
//! soc/uart/uart_regs.veryl, as soc_top wires them: 115200 8N1 at 18 MHz (156 clocks per
//! bit), 32-byte TX and RX FIFOs, liteuart's registers (`read`/`write`: 32-bit words
//! decoded on addr[7:2], repeating every 256 bytes, byte enables not looked at): 0x00 RXTX
//! (a read pops RX, a write pushes TX), 0x04 TXFULL, 0x08 RXEMPTY, 0x0C EV_STATUS (bit 0
//! TX = TX FIFO not full, bit 1 RX = RX FIFO not empty), 0x10 EV_PENDING (EV_STATUS while
//! EV_ENABLE is 0, EV_STATUS & EV_ENABLE otherwise: Linux's polled liteuart never enables
//! anything and reads the levels there, Zephyr's uart_console ISR loops while it shows an
//! event and needs a disabled one hidden), 0x14 EV_ENABLE (2 bits; reset 0, and 0 again at
//! the CPU's soft reset, `soft_reset`), 0x18 TXEMPTY (the TX FIFO is empty), 0x1C RXFULL; the rest read 0 and
//! ignore writes. The events are levels, not latched: EV_PENDING ignores writes, and the
//! machine external interrupt is `EV_STATUS & EV_ENABLE != 0` (`irq`). A byte that arrives
//! with the RX FIFO full is dropped and a frame with a low stop bit is kept, both without a
//! trace in any register.
//!
//! `legacy_read`/`legacy_write`/`rx_pending` are the register view before it (v0.3.x's
//! bitstreams, less their sticky error bits): data at +0, status at +4 (bit 0 rx_empty,
//! bit 1 tx_full), the interrupt while the RX FIFO holds data; only `Soc::v03_mmio` uses it.
//!
//! Timing (all derived from the RTL, and proven against it by the lockstep tests):
//! - The TX bit timer free-runs from reset: its alarm fires in cycles 155 + 156n (BIT - 1 + BIT n).
//!   uart_tx leaves RST at the first alarm; a byte popped from the FIFO while it is
//!   idle is loaded at the next alarm, its start bit begins the cycle after, and the
//!   transmitter is idle again 9 bit times + 2 cycles after the load.
//! - A byte whose start bit begins in cycle s is pushed into the RX FIFO at the end of
//!   cycle s + 1485 (the middle of its stop bit: 2 synchronizer stages and the
//!   IDLE cycle, BIT/2 to mid start bit, then 9 bit times), or dropped if the FIFO is
//!   full then.
//! - The CPU reads a register in its MEM_WAIT cycle (combinational); the pop happens at
//!   the end of that cycle. A store (TX push, EV_ENABLE) takes effect at the end of its
//!   cycle, so the interrupt follows from the next.
//!
//! Everything that happens at one clock edge is decided from the state during that
//! cycle; CPU effects are therefore queued and applied at their edge, after the UART's
//! own events have been evaluated.

use std::collections::VecDeque;

/// Clocks per bit: soc_pkg::UART_CNT = 18_000_000 / 115_200.
pub const BIT: u64 = 156;
/// Clocks per 8N1 frame (start, 8 data, stop).
pub const FRAME: u64 = 10 * BIT;
pub const FIFO_DEPTH: usize = 32;
/// First TX bit-timer alarm (clk_timer counts down from CNT - 1 after reset).
const FIRST_ALARM: u64 = BIT - 1;
/// Start of the start bit -> RX FIFO push edge (see the module docs).
const RX_PUSH_DELAY: u64 = 3 + BIT / 2 + 9 * BIT;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CpuOp {
    TxPush(u8),
    RxPop,
    /// EV_ENABLE is written (or cleared by the soft reset).
    SetEvEnable(u8),
}

/// A byte on the RX line: its value and the cycle its start bit begins.
#[derive(Clone, Copy, Debug)]
struct RxFrame {
    byte: u8,
    start: u64,
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
    /// Bytes dropped because they arrived with the RX FIFO full (for the host's tests; no
    /// register shows it).
    rx_dropped: u64,
    /// CPU effects waiting for their edge: (edge cycle, op), in time order.
    cpu_ops: VecDeque<(u64, CpuOp)>,
    /// No edge before this cycle changes anything (`advance`'s fast path; 0 = unknown).
    quiet_until: u64,
    /// EV_ENABLE (bit 0 TX, bit 1 RX).
    ev_enable: u8,
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
            rx_dropped: 0,
            cpu_ops: VecDeque::new(),
            quiet_until: 0,
            ev_enable: 0,
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
    #[inline]
    pub fn advance(&mut self, to: u64) {
        // Nothing happens at the edges before `quiet_until` (called every instruction)
        if to <= self.quiet_until {
            self.now = self.now.max(to);
        } else {
            self.advance_edges(to);
        }
    }

    #[inline(never)]
    fn advance_edges(&mut self, to: u64) {
        self.quiet_until = 0; // unknown again unless the loop below finds the next edge
        while self.now < to {
            // Next edge at which something happens (all < `to` or we stop)
            let tx_pop = (!self.tx_fifo.is_empty()).then(|| self.now.max(self.tx_idle_from));
            let rx_push = self.rx_line.front().map(|f| f.start + RX_PUSH_DELAY);
            let cpu = self.cpu_ops.front().map(|op| op.0);
            let Some(edge) = [tx_pop, rx_push, cpu].into_iter().flatten().min() else {
                self.now = to;
                self.quiet_until = u64::MAX;
                return;
            };
            if edge >= to {
                self.now = to;
                self.quiet_until = edge;
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
                self.rx_dropped += 1;
            } else {
                pushed = Some(f.byte);
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
                CpuOp::SetEvEnable(v) => self.ev_enable = v,
            }
        }
        if let Some(b) = pushed {
            self.rx_fifo.push_back(b);
        }
    }

    fn queue(&mut self, edge: u64, op: CpuOp) {
        self.cpu_ops.push_back((edge, op));
        self.quiet_until = self.quiet_until.min(edge);
    }

    /// EV_STATUS (state advanced to the cycle).
    fn events(&self) -> u32 {
        let tx = self.tx_fifo.len() < FIFO_DEPTH;
        let rx = !self.rx_fifo.is_empty();
        tx as u32 | (rx as u32) << 1
    }

    /// EV_STATUS & EV_ENABLE, the interrupt's events (state advanced to the cycle).
    fn enabled(&self) -> u32 {
        self.ev_enable as u32 & self.events()
    }

    /// EV_PENDING: EV_STATUS while EV_ENABLE is 0, else EV_STATUS & EV_ENABLE (state
    /// advanced to the cycle).
    fn pending(&self) -> u32 {
        if self.ev_enable == 0 {
            self.events()
        } else {
            self.enabled()
        }
    }

    /// Queue the pop of the RX FIFO's head at the end of cycle `c`, if there is one, and
    /// return it (0 when empty).
    fn pop_head(&mut self, c: u64) -> u32 {
        let head = self.rx_fifo.front().copied();
        if head.is_some() {
            self.queue(c, CpuOp::RxPop);
        }
        head.unwrap_or(0) as u32
    }

    /// Queue a TX push at the end of cycle `c` (dropped when the TX FIFO is full).
    fn push_tx(&mut self, v: u32, c: u64) {
        self.advance(c);
        if self.tx_fifo.len() < FIFO_DEPTH {
            self.queue(c, CpuOp::TxPush(v as u8));
        }
    }

    /// CPU read in cycle `c` (its MEM_WAIT cycle).
    pub fn read(&mut self, addr: u32, c: u64) -> u32 {
        self.advance(c);
        match addr & 0xFC {
            0x00 => self.pop_head(c),
            0x04 => (self.tx_fifo.len() == FIFO_DEPTH) as u32,
            0x08 => self.rx_fifo.is_empty() as u32,
            0x0C => self.events(),
            0x10 => self.pending(),
            0x14 => self.ev_enable as u32,
            0x18 => self.tx_fifo.is_empty() as u32,
            0x1C => (self.rx_fifo.len() == FIFO_DEPTH) as u32,
            _ => 0,
        }
    }

    /// CPU write at the end of cycle `c` (RXTX transmits when the TX FIFO isn't full;
    /// EV_ENABLE takes bits 1:0; the rest ignore writes).
    pub fn write(&mut self, addr: u32, v: u32, c: u64) {
        match addr & 0xFC {
            0x00 => self.push_tx(v, c),
            0x14 => {
                self.advance(c);
                self.queue(c, CpuOp::SetEvEnable((v & 3) as u8));
            }
            _ => {}
        }
    }

    /// The CPU's soft reset, its pulse covering cycle `c` (after every store before it):
    /// EV_ENABLE is cleared at the end of `c`; the FIFOs and the line go on.
    pub fn soft_reset(&mut self, c: u64) {
        self.advance(c);
        self.queue(c, CpuOp::SetEvEnable(0));
    }

    /// The machine external interrupt during cycle `c` (EV_STATUS & EV_ENABLE != 0).
    #[inline]
    pub fn irq(&mut self, c: u64) -> bool {
        self.advance(c);
        self.enabled() != 0
    }

    /// The register view before liteuart's (`Soc::v03_mmio`): CPU read in cycle `c`, data
    /// at offset 0 (pops a byte), status at any other offset (no side effect).
    pub fn legacy_read(&mut self, addr: u32, c: u64) -> u32 {
        self.advance(c);
        if addr & 0xF == 0 {
            self.pop_head(c)
        } else {
            ((self.tx_fifo.len() == FIFO_DEPTH) as u32) << 1 | self.rx_fifo.is_empty() as u32
        }
    }

    /// The register view before liteuart's: CPU write at the end of cycle `c`, only
    /// offset 0 transmits.
    pub fn legacy_write(&mut self, addr: u32, v: u32, c: u64) {
        if addr & 0xF == 0 {
            self.push_tx(v, c);
        }
    }

    /// Bytes in the RX FIFO during cycle `c`.
    pub fn rx_level(&mut self, c: u64) -> usize {
        self.advance(c);
        self.rx_fifo.len()
    }

    /// Bytes dropped by cycle `c` because they arrived with the RX FIFO full. Observation
    /// only: the RTL keeps no such count and no register shows a drop.
    pub fn rx_dropped(&mut self, c: u64) -> u64 {
        self.advance(c);
        self.rx_dropped
    }

    /// True while the RX FIFO holds data during cycle `c` (the MEI line of the view before
    /// liteuart's, `Soc::v03_mmio`).
    #[inline]
    pub fn rx_pending(&mut self, c: u64) -> bool {
        self.advance(c);
        !self.rx_fifo.is_empty()
    }

    /// The host sends `bytes` on the RX line, back to back, starting no earlier than
    /// cycle `c`. `bad_stop` sends them with a low stop bit (a framing error), which
    /// changes nothing the CPU sees: uart_rx delivers the byte all the same.
    /// Panics if a byte would reach the RX FIFO at an edge `advance` has already passed.
    pub fn host_send(&mut self, bytes: &[u8], c: u64, _bad_stop: bool) {
        for &byte in bytes {
            let start = self.rx_line_free.max(c);
            // Applied late, its edge would move `now` back and shift every later edge
            assert!(
                start + RX_PUSH_DELAY >= self.now,
                "UART byte starting in cycle {start} reaches the RX FIFO before cycle {}, \
                 which the UART has already passed",
                self.now
            );
            self.rx_line.push_back(RxFrame { byte, start });
            self.rx_line_free = start + FRAME;
            self.quiet_until = self.quiet_until.min(start + RX_PUSH_DELAY);
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

    /// Earliest cycle in which more than `n` bytes can have completely left the pin, when
    /// `n` have by cycle `c` (the state must have been advanced to `c`). A byte not yet
    /// popped from the TX FIFO, even one the CPU hasn't written yet, is popped at an edge
    /// e >= c and starts after the next alarm, at e + 2 or later.
    pub fn next_tx_complete(&self, n: usize, c: u64) -> u64 {
        let unpopped = c + 2 + FRAME;
        self.tx_out
            .get(n)
            .map_or(unpopped, |&(_, s)| (s + FRAME).min(unpopped))
    }
}
