# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
pytest entry point for every cocotb testbench (sim/runners/test_sim.py).

Each parametrized case compiles (if needed) and runs one cocotb test module from
sim/unit/ or sim/integration/ through sim_runner.run(). Select with node IDs, e.g.
    pytest -s "sim/runners/test_sim.py::test_soc[test_soc_fast]"
or with markers (see pyproject.toml): -m unit, -m gls, -m "not slow".
The simulator is $SIM (default icarus).
"""

import pytest
from sim_runner import run

slow = pytest.mark.slow

# (toplevel, cocotb test module)
UNIT = [
    ("rv32i_alu", "test_rv32i_alu"),
    ("rv32i_decode", "test_rv32i_decode"),
    ("hack_translator", "test_hack_translator"),
    ("rv32i_regfile", "test_rv32i_regfile"),
    ("rv32i_csrs", "test_rv32i_csrs"),
    ("auto_mode_detector", "test_auto_mode_detector"),
    ("unified_cpu", "test_unified_cpu"),
    ("unified_cpu", "test_hack_cpu_ops"),
    ("unified_cpu", "test_rv32i_compliance"),
    ("clk_timer", "test_clk_timer"),
    ("shift_registers", "test_shift_registers"),
    ("fifo_sync", "test_fifo_sync"),
    ("uart_tx", "test_uart_tx"),
    ("uart_rx", "test_uart_rx"),
    ("uart_controller", "test_uart_controller"),
]

# Gate-level: netlists from `make synth-units`
UNIT_GLS = [
    ("unified_cpu", "test_unified_cpu"),
    ("unified_cpu", "test_hack_cpu_ops"),
    ("unified_cpu", "test_rv32i_compliance"),
    ("uart_controller", "test_uart_controller"),
    ("auto_mode_detector", "test_auto_mode_detector"),
]

# Full SoC. Tests on sim/integration/soc_env.py use the tb_soc_top.sv clock wrapper;
# the rest still drive their own clock on a bare soc_top.
SOC = [
    ("tb_soc_top", "test_soc_boot"),
    ("soc_top", "test_soc_rv32i"),
    ("tb_soc_top", "test_soc_hack"),
    ("soc_top", "test_soc_zephyr"),
    ("tb_soc_top", "test_soc_fast"),
    pytest.param("tb_soc_top", "test_soc_hardware_flow", marks=slow),
]

# Full-SoC gate-level: netlist from `make synth-top`
SOC_GLS = [
    ("tb_soc_top", "test_soc_gls_fast"),
    pytest.param("tb_soc_top", "test_soc_hardware_flow", marks=slow),
]


def _ids(cases):
    return [c.values[1] if hasattr(c, "values") else c[1] for c in cases]


@pytest.mark.unit
@pytest.mark.parametrize("toplevel,module", UNIT, ids=_ids(UNIT))
def test_unit(toplevel, module):
    run(toplevel, module)


@pytest.mark.unit
@pytest.mark.gls
@pytest.mark.parametrize("toplevel,module", UNIT_GLS, ids=_ids(UNIT_GLS))
def test_unit_gls(toplevel, module):
    run(toplevel, module, gls=True)


@pytest.mark.soc
@pytest.mark.parametrize("toplevel,module", SOC, ids=_ids(SOC))
def test_soc(toplevel, module):
    run(toplevel, module)


@pytest.mark.soc
@pytest.mark.gls
@pytest.mark.parametrize("toplevel,module", SOC_GLS, ids=_ids(SOC_GLS))
def test_soc_gls(toplevel, module):
    run(toplevel, module, gls=True)
