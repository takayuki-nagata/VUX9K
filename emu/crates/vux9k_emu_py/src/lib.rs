// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Python bindings of the VUX9K emulator: `import vux9k_emu` (see sim/emu/).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

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

    fn __repr__(&self) -> String {
        format!(
            "<vux9k_emu.Soc {} cycle={} pc={:#010x}>",
            self.inner.profile.describe(),
            self.inner.cycle,
            self.inner.pc
        )
    }
}

#[pymodule]
fn vux9k_emu(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Soc>()
}
