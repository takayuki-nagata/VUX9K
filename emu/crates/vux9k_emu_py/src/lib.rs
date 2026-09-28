// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Python bindings of the VUX9K emulator: `import vux9k_emu` (see sim/emu/).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use emu_core::sdcard::{Faults, SdCard};
use emu_core::{Profile, Stop};

fn parse_profile(name: &str) -> PyResult<Profile> {
    match name {
        "real" => Ok(Profile::Real),
        "extended" => Ok(Profile::EXTENDED),
        "isa-test" => Ok(Profile::IsaTest),
        _ => Err(PyValueError::new_err(format!(
            "unknown profile {name:?} (real, extended, isa-test)"
        ))),
    }
}

/// The emulated SoC.
#[pyclass(unsendable, module = "vux9k_emu")]
struct Soc {
    inner: emu_core::Soc,
}

#[pymethods]
impl Soc {
    #[new]
    #[pyo3(signature = (profile = "real"))]
    fn new(profile: &str) -> PyResult<Self> {
        Ok(Soc {
            inner: emu_core::Soc::new(parse_profile(profile)?),
        })
    }

    /// Profile description (says NOT REAL HARDWARE for the extended profile).
    #[getter]
    fn profile(&self) -> String {
        self.inner.profile.describe()
    }

    /// Load bytes into I-RAM at byte address `addr`.
    fn load_iram(&mut self, addr: u32, data: &[u8]) {
        self.inner.load_iram(addr, data);
    }

    /// Load 32-bit words into D-RAM from word 0.
    fn load_dram_words(&mut self, words: Vec<u32>) {
        self.inner.load_dram_words(&words);
    }

    /// soc_ram's power-on preload, from the text of firmware.hex and firmware_d0-3.hex.
    fn load_readmemh(
        &mut self,
        iram: &str,
        d0: &str,
        d1: &str,
        d2: &str,
        d3: &str,
    ) -> PyResult<()> {
        self.inner
            .load_readmemh(iram, [d0, d1, d2, d3])
            .map_err(PyValueError::new_err)
    }

    /// Write 32-bit words into I-RAM from word index `start`.
    #[pyo3(signature = (words, start = 0))]
    fn load_iram_words(&mut self, words: Vec<u32>, start: u32) {
        self.inner.load_iram_words(start, &words);
    }

    fn iram_word(&self, addr: u32) -> u32 {
        self.inner.iram_word(addr)
    }

    fn dram_word(&self, index: u32) -> u32 {
        self.inner.dram_word_index(index)
    }

    /// Reset the CPU (memories keep their contents).
    fn reset(&mut self) {
        self.inner.reset();
    }

    /// Execute one instruction; returns what it did as a dict.
    fn step<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let r = self.inner.step();
        let d = PyDict::new(py);
        d.set_item("cycle", r.cycle)?;
        d.set_item("riscv", r.riscv)?;
        d.set_item("pc", r.pc)?;
        d.set_item("instr", r.instr)?;
        d.set_item("rd", r.rd)?;
        d.set_item("rd2", r.rd2)?;
        d.set_item("store", r.store)?;
        d.set_item("trap", r.trap)?;
        d.set_item("branch_taken", r.branch_taken)?;
        d.set_item("cycles", r.cycles)?;
        Ok(d)
    }

    /// Run up to `max_cycles` clock cycles. Returns ("budget", None) or ("tohost", value).
    fn run(&mut self, max_cycles: u64) -> (&'static str, Option<u32>) {
        match self.inner.run(max_cycles) {
            Stop::Budget => ("budget", None),
            Stop::ToHost(v) => ("tohost", Some(v)),
        }
    }

    /// Run until the host has received `needle` on the UART at or after output index
    /// `start`; returns the index just past it, or None after `max_cycles`.
    #[pyo3(signature = (needle, max_cycles, start = 0))]
    fn run_until_tx(&mut self, needle: &[u8], max_cycles: u64, start: usize) -> Option<usize> {
        self.inner.run_until_tx(needle, start, max_cycles)
    }

    // ----- UART ---------------------------------------------------------------------

    /// The host sends `data` on the UART RX line from now on, back to back
    /// (`bad_stop`: with a low stop bit, a framing error).
    /// `at`: start no earlier than this cycle instead of now.
    #[pyo3(signature = (data, bad_stop = false, at = None))]
    fn uart_send(&mut self, data: &[u8], bad_stop: bool, at: Option<u64>) {
        let c = at.unwrap_or(self.inner.cycle);
        self.inner.periph.uart.host_send(data, c, bad_stop);
    }

    /// Cycle by which everything sent so far has reached the RX FIFO.
    #[getter]
    fn uart_send_done(&self) -> u64 {
        self.inner.periph.uart.host_send_done()
    }

    /// Bytes waiting in the RX FIFO now.
    #[getter]
    fn uart_rx_level(&mut self) -> usize {
        let c = self.inner.cycle;
        self.inner.periph.uart.rx_level(c)
    }

    /// The UART's sticky overrun flag now (a byte arrived with the RX FIFO full).
    #[getter]
    fn uart_overrun(&mut self) -> bool {
        let c = self.inner.cycle;
        self.inner.periph.uart.overrun(c)
    }

    /// UART output the host has completely received.
    fn uart_received<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.inner.uart_received())
    }

    /// Every transmitted byte with the cycle its start bit began.
    fn uart_tx_log(&mut self) -> Vec<(u8, u64)> {
        let c = self.inner.cycle;
        self.inner.periph.uart.advance(c);
        self.inner.periph.uart.tx_log().to_vec()
    }

    // ----- GPIO ---------------------------------------------------------------------

    /// Press (True) or release button S2 from now on (or from cycle `at`; one pending
    /// change at a time).
    #[pyo3(signature = (pressed, at = None))]
    fn set_button(&mut self, pressed: bool, at: Option<u64>) {
        let c = at.unwrap_or(self.inner.cycle);
        self.inner.periph.gpio.set_button(c, pressed);
    }

    /// LED register (bit set = lit).
    #[getter]
    fn leds(&self) -> u8 {
        self.inner.periph.gpio.leds()
    }

    // ----- SD card ------------------------------------------------------------------

    /// Insert a card holding the raw sector image `image`.
    #[pyo3(signature = (image = b"".as_slice(), sdhc = true, strict = false, mute_cmds = vec![],
                        never_ready = false, read_error = false, write_reject = false,
                        bad_sectors = vec![]))]
    #[allow(clippy::too_many_arguments)]
    fn sd_insert(
        &mut self,
        image: &[u8],
        sdhc: bool,
        strict: bool,
        mute_cmds: Vec<u8>,
        never_ready: bool,
        read_error: bool,
        write_reject: bool,
        bad_sectors: Vec<u32>,
    ) {
        let mut card = SdCard::new(image.to_vec());
        card.sdhc = sdhc;
        card.strict = strict;
        card.faults = Faults {
            mute_cmds,
            never_ready,
            read_error,
            bad_sectors,
            write_reject,
        };
        self.inner.periph.sd.card = Some(card);
    }

    fn sd_remove(&mut self) {
        self.inner.periph.sd.card = None;
    }

    fn sd_image<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, self.card()?.image()))
    }

    fn sd_sector<'py>(&self, py: Python<'py>, lba: u32) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, &self.card()?.sector(lba)))
    }

    fn sd_set_sector(&mut self, lba: u32, data: &[u8]) -> PyResult<()> {
        self.card_mut()?.set_sector(lba, data);
        Ok(())
    }

    /// (cmd, arg) of every command the card received.
    #[getter]
    fn sd_commands(&self) -> PyResult<Vec<(u8, u32)>> {
        Ok(self.card()?.commands.clone())
    }

    #[getter]
    fn sd_violations(&self) -> PyResult<Vec<String>> {
        Ok(self.card()?.violations.clone())
    }

    #[getter]
    fn sd_idle_clocks(&self) -> PyResult<u64> {
        Ok(self.card()?.idle_clocks())
    }

    #[getter]
    fn cycle(&self) -> u64 {
        self.inner.cycle
    }

    #[getter]
    fn steps(&self) -> u64 {
        self.inner.steps
    }

    #[getter]
    fn pc(&self) -> u32 {
        self.inner.pc
    }

    #[getter]
    fn regs(&self) -> Vec<u32> {
        self.inner.regs.to_vec()
    }

    #[getter]
    fn riscv_mode(&self) -> bool {
        self.inner.riscv_mode
    }

    #[getter]
    fn mcause(&self) -> u32 {
        self.inner.csr.mcause
    }

    #[getter]
    fn mepc(&self) -> u32 {
        self.inner.csr.mepc
    }

    #[getter]
    fn mtval(&self) -> u32 {
        self.inner.csr.mtval
    }

    #[getter]
    fn mstatus(&self) -> u32 {
        self.inner.csr.mstatus | 0x1800 // MPP reads as M
    }

    fn __repr__(&self) -> String {
        format!(
            "<vux9k_emu.Soc {} cycle={} pc={:#010x}>",
            self.inner.profile.describe(),
            self.inner.cycle,
            self.inner.pc
        )
    }
}

impl Soc {
    fn card(&self) -> PyResult<&SdCard> {
        self.inner
            .periph
            .sd
            .card
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("no SD card inserted"))
    }

    fn card_mut(&mut self) -> PyResult<&mut SdCard> {
        self.inner
            .periph
            .sd
            .card
            .as_mut()
            .ok_or_else(|| PyRuntimeError::new_err("no SD card inserted"))
    }
}

#[pymodule]
fn vux9k_emu(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Soc>()
}
