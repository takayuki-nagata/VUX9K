#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Mutation testing of one RTL module with mcy (`make mutation MCY_TOP=<module>`; manual only).

mcy takes the module's generated .sv (flattened with its submodules), lists random
single-point mutations, and for each one:
  1. test_eq: Yosys `equiv_*` checks whether the mutant is equivalent to the original
     (a mutation nothing could observe); equivalent ones are left out of the score.
  2. test_sim: the module's cocotb unit tests (Icarus) run against the mutant, which
     sim_runner swaps in for the generated file (VUX9K_RTL_OVERRIDE). unified_cpu also
     runs the riscv-tests. A failing (or timed-out) run detects the mutant.
Before mcy runs, the unmutated module goes through the same path and must pass: a mutant
that only fails because the harness is broken (say, Icarus can't compile what Yosys wrote)
would otherwise count as detected. A mutant that doesn't compile stops the run.
The project is build/mcy/<module>/; the result, with the source location of every
surviving mutant, is written to build/mcy/<module>/summary.md and printed. A survivor
is a gap in the tests: add a test, or explain why the mutant is harmless.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "sim" / "runners"))
import sim_runner  # noqa: E402
import test_sim  # noqa: E402

MCY_DIR = REPO_ROOT / "build" / "mcy"
# Modules worth mutating (Hack-only ones go away in Rev.B). soc_ram/soc_top need firmware
# files at elaboration and aren't unit-sized.
TARGETS = (
    "rv32i_alu",
    "rv32i_decode",
    "next_pc_unit",
    "rv32i_lsu",
    "rv32i_trap_unit",
    "rv32i_csrs",
    "soc_addr_decoder",
    "sdcard_spi",
    "uart_rx",
    "fifo_sync",
    "unified_cpu",
)
# Per mutant: far above a normal run, so a hang counts as detected rather than stalling mcy
SIM_TIMEOUT_S = 600

CONFIG = """\
[options]
size {size}
seed {seed}
tags DETECTED SURVIVED EQUIVALENT

[script]
read_verilog -sv {sources}
prep -top {module}
flatten
hierarchy -top {module}

[logic]
if result("test_eq") == "PASS":
    tag("EQUIVALENT")
elif result("test_sim") == "FAIL":
    tag("DETECTED")
else:
    tag("SURVIVED")

[report]
n = tags("DETECTED") + tags("SURVIVED")
print("Detected %d of %d non-equivalent mutants (%.1f%%), %d equivalent" % (
    tags("DETECTED"), n, 100.0 * tags("DETECTED") / max(n, 1), tags("EQUIVALENT")))

[test test_eq]
expect PASS FAIL
run bash $PRJDIR/test_eq.sh

[test test_sim]
expect PASS FAIL
run bash $PRJDIR/test_sim.sh
"""

# mcy's input.txt line: "<idx> mutate <args>". $pmux cells are lowered to muxes, or
# write_verilog emits them as functions Icarus can't compile.
MUTATE = (
    'yosys -ql mutate.log -p "read_rtlil {design}; $(cut -d" " -f2- input.txt | head -1); '
    'pmuxtree; opt_clean; write_verilog -noattr {out}"'
)

# PASS = the mutant is equivalent to the original (proven by induction, 4 steps; memories
# become flip-flops and async resets synchronous, which equiv_* needs)
TEST_EQ = """\
#!/bin/bash
exec 2>&1
set -e
idx=$(awk '{{print $1; exit}}' input.txt)
{mutate_il}
if yosys -ql eq.log -p "read_rtlil ../../database/design.il; rename {module} gold; read_rtlil mutated.il; \\
        rename {module} gate; memory_map; async2sync; opt_clean; equiv_make gold gate equiv; \\
        hierarchy -top equiv; equiv_simple -seq 4; equiv_induct -seq 4; equiv_status -assert"; then
    echo "$idx PASS" > output.txt
else
    echo "$idx FAIL" > output.txt
fi
"""

# PASS = every test passed, i.e. the mutant survived
TEST_SIM = """\
#!/bin/bash
exec 2>&1
set -e
idx=$(awk '{{print $1; exit}}' input.txt)
task=$PWD
# mcy runs under OSS CAD Suite's own Python (tabbypy3), whose settings break ours
unset PYTHONHOME PYTHONEXECUTABLE PYTHONNOUSERSITE
{mutate_v}
iverilog -g2012 -o /dev/null -s {module} mutated.v  # not compiling stops mcy (set -e)
cd {repo}
export VUX9K_RTL_OVERRIDE={module}=$task/mutated.v VUX9K_MUT_BUILD_DIR=$task/sim SIM=icarus
result=PASS
timeout {timeout} {python} -m pytest -q -p no:cacheprovider {nodes} > "$task/sim.out" 2>&1 || result=FAIL
{extra}
echo "$idx $result" > "$task/output.txt"
mkdir -p "$PRJDIR/logs" && cp "$task/sim.out" "$PRJDIR/logs/$MUTATIONS.out"  # mcy deletes the task directory
rm -rf "$task/sim"
"""
# unified_cpu: the riscv-tests too (mcy runs its mutants one at a time: run_riscv_tests.py
# regenerates build/veryl and the test images, shared by all)
RISCV_TESTS = (
    '[ $result = FAIL ] || timeout {timeout} {python} scripts/run_riscv_tests.py >> "$task/sim.out" 2>&1 || result=FAIL'
)


def sources():
    """The generated RTL files, packages first; Yosys keeps only what module uses."""
    skip = {"soc_ram", "soc_top"}
    return [str(p) for p in sim_runner.RTL_SOURCES if p.stem not in skip]


def unit_tests(module):
    return [f"sim/runners/test_sim.py::test_unit[{m}]" for top, m in test_sim.UNIT if top == module]


def setup(module, size, seed):
    prj = MCY_DIR / module
    shutil.rmtree(prj, ignore_errors=True)
    prj.mkdir(parents=True)
    nodes = unit_tests(module)
    if not nodes:
        sys.exit(f"{module}: no unit tests in sim/runners/test_sim.py's UNIT")
    (prj / "config.mcy").write_text(CONFIG.format(size=size, seed=seed, sources=" ".join(sources()), module=module))
    design = "../../database/design.il"
    mutate_il = MUTATE.format(design=design, out="mutated.il").replace("write_verilog -noattr", "write_rtlil")
    (prj / "test_eq.sh").write_text(TEST_EQ.format(module=module, mutate_il=mutate_il))
    fmt = {"timeout": SIM_TIMEOUT_S, "python": sys.executable}
    extra = RISCV_TESTS.format(**fmt) if module == "unified_cpu" else ""
    (prj / "test_sim.sh").write_text(
        TEST_SIM.format(
            repo=REPO_ROOT,
            module=module,
            nodes=" ".join(nodes),
            extra=extra,
            mutate_v=MUTATE.format(design=design, out="mutated.v"),
            **fmt,
        )
    )
    return prj


def selftest(prj):
    """The unmutated module through test_sim.sh, in the environment mcy gives it (OSS CAD
    Suite's tabbypy3 wrapper, which mcy runs under): it must pass, or the harness is broken."""
    task = prj / "tasks" / "selftest"
    task.mkdir(parents=True)
    (task / "input.txt").write_text("1 mutate -mode none\n")  # a no-op, in mcy's format
    script = f"import subprocess, sys; sys.exit(subprocess.run(['bash', {str(prj / 'test_sim.sh')!r}]).returncode)"
    wrapper = Path(shutil.which("mcy") or "mcy").resolve().parent / "tabbypy3"
    cmd = [str(wrapper), "-c", script] if wrapper.exists() else ["bash", str(prj / "test_sim.sh")]
    env = os.environ | {"PRJDIR": str(prj), "MUTATIONS": "selftest"}
    subprocess.run(cmd, cwd=task, check=True, env=env)
    result = (task / "output.txt").read_text().split()
    if result != ["1", "PASS"]:
        sys.exit(f"harness self-test: the unmutated module failed its tests; see {task / 'sim.out'}")
    shutil.rmtree(task)
    print("[INFO] Harness self-test OK (the unmutated module passes)")


def summarize(module, prj):
    db = sqlite3.connect(prj / "database" / "db.sqlite3")
    tags = dict(db.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag").fetchall())
    survivors = db.execute(
        "SELECT m.mutation_id, m.mutation, GROUP_CONCAT(o.opt_value, ' ') FROM mutations m "
        "JOIN tags t ON t.mutation_id = m.mutation_id AND t.tag = 'SURVIVED' "
        "LEFT JOIN options o ON o.mutation_id = m.mutation_id AND o.opt_type = 'src' "
        "GROUP BY m.mutation_id ORDER BY m.mutation_id"
    ).fetchall()
    detected, survived, equivalent = (tags.get(t, 0) for t in ("DETECTED", "SURVIVED", "EQUIVALENT"))
    n = detected + survived
    lines = [
        f"# Mutation testing: {module}",
        "",
        f"Detected {detected} of {n} non-equivalent mutants ({100 * detected / max(n, 1):.1f}%); "
        f"{equivalent} equivalent (left out).",
        "",
    ]
    if survivors:
        lines += ["| id | source (generated .sv) | mutation |", "|---|---|---|"]
        lines += [f"| {i} | {src or ''} | `{mut}` |" for i, mut, src in survivors]
    (prj / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="+", help=f"modules, or 'all' ({', '.join(TARGETS)})")
    ap.add_argument("--size", type=int, default=100, help="mutants per module")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    modules = list(TARGETS) if args.modules == ["all"] else args.modules
    for module in modules:
        if module not in TARGETS:
            sys.exit(f"{module}: not a mutation target ({', '.join(TARGETS)})")
    for module in modules:
        prj = setup(module, args.size, args.seed)
        jobs = 1 if module == "unified_cpu" else args.jobs
        subprocess.run(["mcy", "init"], cwd=prj, check=True)
        selftest(prj)
        subprocess.run(["mcy", "run", "-j", str(jobs)], cwd=prj, check=True)
        summarize(module, prj)


if __name__ == "__main__":
    main()
