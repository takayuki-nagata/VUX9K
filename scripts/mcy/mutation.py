#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Mutation testing of one RTL module with mcy (`make mutation MCY_TOP=<module>`; manual only).

mcy takes the module's generated .sv (flattened with its submodules), lists random
single-point mutations, and for each one:
  1. test_eq: whether the mutant is equivalent to the original (a mutation nothing could
     observe); equivalent ones are left out of the score. First Yosys `equiv_*` (induction
     from any state, fast); if that fails, a module with clk and rst gets a proof from
     reset (sby, `abc pdr`): a mutant that differs only in states the module can't reach,
     such as a counter taking a different path to the same end, is equivalent too. The
     proof uses the module's default parameters.
  2. test_sim: the module's cocotb unit tests (Icarus) run against the mutant, which
     sim_runner swaps in for the generated file (VUX9K_RTL_OVERRIDE). unified_cpu also
     runs the riscv-tests. A failing (or timed-out) run detects the mutant.
Before mcy runs, the unmutated module goes through the same path and must pass: a mutant
that only fails because the harness is broken (say, Icarus can't compile what Yosys wrote)
would otherwise count as detected. A mutant that doesn't compile stops the run. The
proof from reset is checked the same way: the original must be equivalent to itself, and
at least one of the first mutants must be found non-equivalent.
The project is build/mcy/<module>/; the result, with the source location of every
surviving mutant, is written to build/mcy/<module>/summary.md and printed. A survivor
is a gap in the tests: add a test, or explain why the mutant is harmless (ACCEPTED).

`--replay ID [ID ...]` runs the unit tests against single mutants of the last run (the
project must exist), keeping each one's mutated.v and test output in
build/mcy/<module>/replay/<id>/; `--node` picks the pytest node(s), and COCOTB_TEST_FILTER
in the environment one cocotb test. `--summarize` rewrites the summaries of the last run
(after editing ACCEPTED).
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "sim" / "runners"))
import sim_runner  # noqa: E402
import test_sim  # noqa: E402

MCY_DIR = REPO_ROOT / "build" / "mcy"
# Modules worth mutating. soc_rom/soc_top need firmware
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
    "soc_icache",
    "soc_dcache",
    "unified_cpu",
)
# Per mutant: far above a normal run, so a hang counts as detected rather than stalling mcy
SIM_TIMEOUT_S = 600
# Per mutant, the proof from reset; one that doesn't finish counts as non-equivalent
EQ_TIMEOUT_S = 150
# Mutants the self-test tries until the proof from reset finds a non-equivalent one
EQ_SELFTEST_TRIES = 10
# Share of the host's memory the equivalence checks running at once may use; a check that
# needs more is stopped and counts as non-equivalent (its mutant goes to the tests)
EQ_MEM_SHARE = 0.6
# Modules whose mutants skip the equivalence check: all go to the tests, so a survivor may
# be an equivalent mutant (read it before adding a test). {module: reason}
NO_EQUIV = {
    "soc_icache": "memory_map makes its 1024 lines 45k flip-flops a side: one check took hours and 1.4 GB",
    "soc_dcache": "memory_map makes its 512 lines 22k flip-flops a side, as soc_icache's",
}

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

# Survivors that change behavior, but only within the specification, keyed by module and
# describe(): {module: {description: reason}}. Each entry needs its own reason; a survivor
# that is merely untested gets a test instead (the rule of `cov:exclude`). summarize()
# lists them apart from the open ones and names entries no survivor matched.
ACCEPTED: dict[str, dict[str, str]] = {
    "uart_rx": {
        # The start bit's half-bit wait (reg_clkcnt from CNT/2 down to 0), with a counter
        # bit read inverted or a next-value bit flipped: counted out, every variant still
        # takes CNT/2 = 217 clocks (and the counter's value is unused in the other states).
        # Equivalent from reset, but too deep for dprove within its timeout.
        "inv Q[4] reg_clkcnt[4] @ uart_rx.sv:27.29-27.39 uart_rx.sv:58.5-70.8": "equivalent: the half-bit wait"
        " still takes 217 clocks (counted out); dprove times out",
        "inv Q[3] reg_clkcnt[3] @ uart_rx.sv:27.29-27.39 uart_rx.sv:58.5-70.8": "equivalent: the half-bit wait"
        " still takes 217 clocks (counted out); dprove times out",
        "cnot1 Y[7] ctrl 2 next_clkcnt[7] @ uart_rx.sv:28.29-28.40 uart_rx.sv:81.9-120.16 uart_rx.sv:89.16-89.16": (
            "equivalent: the half-bit wait still takes 217 clocks (counted out); dprove times out"
        ),
        "cnot0 B[3] ctrl 1 @ uart_rx.sv:108.21-114.24 uart_rx.sv:108.25-108.58": "equivalent: changes the bit"
        " count (8 -> 0) only on the stop bit, as the receiver leaves RECEIVING; it is set to 0 before it is"
        " read again; dprove times out",
        # data is the shift register's contents; it is only meaningful with rdy (uart_controller
        # stores it into the RX FIFO on rdy). These change it at other times only.
        "const1 A[0] @ uart_rx.sv:91.17-103.20 uart_rx.sv:91.21-91.37": "clears the bit timer all through the"
        " start-bit wait instead of at its end: only the timer's phase while idle changes, i.e. when the"
        " shift register shifts between frames; data differs only without rdy, never in a received byte",
        "inv Q[0] data[0] @ shift_registers.sv:19.5-31.8 uart_rx.sv:13.34-13.38": "data[0] toggles while the"
        " shift register holds; CNT (434 here, 156 in the SoC) is even, so it is back to the right value on"
        " every rdy; data differs only without rdy",
    },
    "unified_cpu": {
        "const0 B[23] @ unified_cpu.sv:603.25-611.28 unified_cpu.sv:603.29-603.42": "clears bit 23 of"
        " r_hack_data in RV32 mode's MEM_WAIT. Only a Hack instruction with M as operand reads it, in"
        " HACK_WB, after its own MEM_WAIT has reloaded it from data_in; the ISA mode changes only through a"
        " reset, which zeroes it. Equivalent from reset, beyond dprove's reach on the whole CPU",
        "cnot1 A[20] ctrl 16 @ unified_cpu.sv:538.13-632.20 unified_cpu.sv:596.20-596.20": "flips bit 20 of"
        " r_hack_data while it holds outside MEM_WAIT and its bit 16 is set. Its value counts only in"
        " HACK_WB, right after MEM_WAIT reloaded it from data_in; in EXECUTE and MEM_WAIT it reaches only"
        " data_out without mem_write (a Hack M write in EXECUTE has no M operand) and, in EXECUTE, the pc_out"
        " of an instruction that goes to MEM_WAIT, an I-RAM read no FETCH uses. Differs only outside the"
        " interface's contract",
        "cnot1 A[27] ctrl 1 @ rv32i_csrs.sv:124.18-175.12 rv32i_csrs.sv:124.22-124.30": "flips bit 27 of"
        " mepc's next value when its bit 1 is set, which never happens: trap entry stores the PC of an RV32"
        " instruction (4-byte aligned; traps never happen in Hack mode, where pc_reg can have bit 1 set)"
        " and CSR writes mask mepc with 0xFFFF_FFFC. Equivalent from reset,"
        " beyond dprove's reach on the whole CPU",
    },
    # The caches skip the equivalence check (NO_EQUIV): these were read in the mutated netlist
    "soc_icache": {
        "const1 B[5] @ soc_icache.sv:124.21-124.42": "no change: bit 5 of the constant 1023 is 1 already",
        "const1 B[0] @ soc_icache.sv:101.13-101.19 soc_icache.sv:101.9-103.12": "no change: the tag write's"
        " enable is `tag_we ? 1 : 0`, its B input 1 already",
        "inv A[30] @ soc_icache.sv:108.13-108.17 soc_icache.sv:108.9-110.12": "changes the word write's data"
        " when fill is 0, i.e. when nothing is written",
        "cnot0 Y[7] ctrl 11 tag_wdata[7] @ soc_icache.sv:82.20-82.29 soc_icache.sv:85.13-85.19"
        " soc_icache.sv:85.9-97.12": "sets bit 7 of a tag only when it is written invalid (bit 11 = 0):"
        " an invalid line misses whatever its other bits",
        "const0 A[0] @ soc_icache.sv:122.17-122.23": "the sweep counter keeps counting after the sweep (while"
        " clear); sweep_idx is read only before swept, and rst zeroes both",
        "cnot1 B[7] ctrl 6 @ soc_icache.sv:122.13-127.16 soc_icache.sv:122.17-122.32": "the sweep counts in"
        " another order (bit 7 of the next index flipped with bit 6) and still clears every line once in"
        " 1024 cycles (counted out); test_reset_sweeps_valid_lines would see a skipped line",
    },
    "soc_dcache": {
        "inv Y[7] @ soc_dcache.sv:112.18-116.12 soc_dcache.sv:112.22-112.26": "sets bit 7 of the tag written"
        " for a partial store miss, which writes it invalid (bit 12 = 0), and of a tag no branch writes",
        "const1 A[10] @ soc_dcache.sv:112.18-116.12 soc_dcache.sv:112.22-112.26": "changes the tag value of"
        " the branch that writes no tag (no access completing)",
        "cnot1 B[8] ctrl 6 @ soc_dcache.sv:141.13-146.16 soc_dcache.sv:141.17-141.32": "the sweep counts in"
        " another order and still clears every line once in 512 cycles (counted out)",
        "cnot1 Q[6] ctrl 4 sweep_idx[6] @ soc_dcache.sv:133.5-151.8 soc_dcache.sv:63.20-63.29": "the sweep"
        " visits the lines in another order (index bit 6 read flipped with bit 4; the register itself"
        " holds) and still clears every line once in 512 cycles (counted out)",
        "cnot1 B[0] ctrl 8 @ soc_dcache.sv:149.50-149.65": "changes conflict_q, which counts only for an"
        " access in the cycle after a write: a write is a MEM_WAIT's last cycle or the sweep, the CPU's"
        " next cycle is no data access, and after the sweep every line is invalid anyway",
        "const1 B[0] @ soc_dcache.sv:149.27-149.65": "conflict_q after every write, whatever the index: counts"
        " only for an access in the cycle after a write, which the CPU never makes (see the entry above)",
    },
    "fifo_sync": {
        "const1 A[1] @ $auto$proc_dff.cc:proc_dff": "sets bit 1 of a memory word while rst is asserted. The"
        " memory has no reset: a word is read as the head only after it has been written, so only rdata of"
        " an empty FIFO differs, which nothing uses (uart_controller pops on re && !empty)",
    },
}

# mcy's input.txt line: "<idx> mutate <args>". $pmux cells are lowered to muxes, or
# write_verilog emits them as functions Icarus can't compile.
MUTATE = (
    'yosys -ql mutate.log -p "read_rtlil {design}; $(cut -d" " -f2- input.txt | head -1); '
    'pmuxtree; opt_clean; write_verilog -noattr {out}"'
)

# PASS = the mutant is equivalent to the original: proven by induction (4 steps; memories
# become flip-flops and async resets synchronous, which equiv_* needs), or else, when
# $PRJDIR/eq_reset.ys exists, from reset (EQ_RESET). EQ_RESET_ONLY=1 skips the induction
# (self-test).
TEST_EQ = """\
#!/bin/bash
exec 2>&1
set -e
idx=$(awk '{{print $1; exit}}' input.txt)
{mutate_il}
ulimit -v {mem_kb}  # the checks only: past it Yosys fails, i.e. not shown equivalent
reset_equiv() {{
    [ -f "$PRJDIR/eq_reset.ys" ] || return 1
    yosys -ql reset.log "$PRJDIR/eq_reset.ys" || return 1
    abc="read_aiger miter.aig; fold; strash; dprove"
    timeout "${{EQ_TIMEOUT:-{eq_timeout}}}" yosys-abc -c "$abc" > dprove.log 2>&1 || true
    grep -q "Networks are equivalent" dprove.log
}}
if [ "${{EQ_RESET_ONLY:-0}}" = 0 ] && yosys -ql eq.log -p "read_rtlil ../../database/design.il; rename {module} gold; \\
        read_rtlil mutated.il; rename {module} gate; memory_map; async2sync; opt_clean; equiv_make gold gate equiv; \\
        hierarchy -top equiv; equiv_simple -seq 4; equiv_induct -seq 4; equiv_status -assert"; then
    echo "$idx PASS" > output.txt
elif reset_equiv; then
    echo "$idx PASS" > output.txt
else
    echo "$idx FAIL" > output.txt
fi
"""

# Equivalence from reset, for modules with clk and rst: the miter of original and mutant
# (same inputs) gets rst asserted in its first step, from equal initial states (all zero:
# storage without a reset, such as a FIFO's memory, must start the same in both), and
# every output must agree after that. The model is built like sby's AIGER model; abc's
# dprove (signal correspondence, then induction/PDR) proves it, which sby's `abc pdr`
# alone couldn't for uart_rx in minutes (its counters). The zero-init is applied to each
# side before the miter: on the miter it would also turn the x constants of
# `-ignore_gold_x`'s mask into 0 and hide real differences (it did, sdcard_spi).
EQ_TOP = """\
module eq_top ({ports});
    reg init = 1'b1;
    always @(posedge clk) init <= 1'b0;
    wire trigger;
    miter m ({conns}, .trigger(trigger));
    always @* if (init) assume (!rst);
    always @* if (!init) assert (!trigger);
endmodule
"""
EQ_RESET = """\
read_rtlil ../../database/design.il
memory_map
setundef -zero -init
rename {module} gold
read_rtlil mutated.il
memory_map
setundef -zero -init
rename {module} gate
miter -equiv -flatten gold gate miter
read_verilog -formal {prj}/eq_top.sv
prep -top eq_top
flatten
async2sync
chformal -assume -early
formalff -setundef -clk2ff -ff2anyinit
chformal -live -fair -cover -remove
setundef -undriven -anyseq
opt -fast
formalff -assume
setattr -unset keep
delete -output
opt -full
techmap
opt -fast
memory_map -formal
formalff -clk2ff -ff2anyinit
simplemap
dffunmap
aigmap
opt_clean
write_aiger -I -B -zinit -no-startoffset miter.aig
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
nodes=${{MUT_NODES:-{nodes}}}
timeout {timeout} {python} -m pytest -q -p no:cacheprovider $nodes > "$task/sim.out" 2>&1 || result=FAIL
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
    skip = {"soc_rom", "soc_top"}
    return [str(p) for p in sim_runner.RTL_SOURCES if p.stem not in skip]


def unit_tests(module):
    return [f"sim/runners/test_sim.py::test_unit[{m}]" for top, m in test_sim.UNIT if top == module]


def eq_mem_kb(jobs):
    """Each equivalence check's memory limit: EQ_MEM_SHARE of the host's, shared by jobs."""
    meminfo = Path("/proc/meminfo").read_text().splitlines()
    total = next(int(ln.split()[1]) for ln in meminfo if ln.startswith("MemTotal:"))
    return int(total * EQ_MEM_SHARE / max(jobs, 1))


def setup(module, size, seed, jobs):
    prj = MCY_DIR / module
    shutil.rmtree(prj, ignore_errors=True)
    prj.mkdir(parents=True)
    nodes = unit_tests(module)
    if not nodes:
        sys.exit(f"{module}: no unit tests in sim/runners/test_sim.py's UNIT")
    (prj / "config.mcy").write_text(CONFIG.format(size=size, seed=seed, sources=" ".join(sources()), module=module))
    design = "../../database/design.il"
    mutate_il = MUTATE.format(design=design, out="mutated.il").replace("write_verilog -noattr", "write_rtlil")
    if module in NO_EQUIV:  # only mcy's no-op mutant (-mode none) is equivalent
        test_eq = (
            "#!/bin/bash\nidx=$(awk '{print $1; exit}' input.txt)\n"
            'if grep -q -- "-mode none" input.txt; then r=PASS; else r=FAIL; fi\necho "$idx $r" > output.txt\n'
        )
    else:
        test_eq = TEST_EQ.format(module=module, mutate_il=mutate_il, eq_timeout=EQ_TIMEOUT_S, mem_kb=eq_mem_kb(jobs))
    (prj / "test_eq.sh").write_text(test_eq)
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


def write_eq_reset(prj, module):
    """The proof from reset's files, if the module has clk and rst (from mcy's design.il)."""
    il = (prj / "database" / "design.il").read_text()
    body = il[il.index(f"module \\{module}\n") :]
    body = body[: body.index("\nend\n")]
    inputs = re.findall(r"^  wire (?:width (\d+) )?input \d+ \\(\S+)$", body, re.M)
    names = [n for _, n in inputs]
    if "clk" not in names or "rst" not in names:
        return False
    ports = ", ".join(f"input [{int(w or 1) - 1}:0] {n}" for w, n in inputs)
    conns = ", ".join(f".in_{n}({n})" for n in names)
    (prj / "eq_top.sv").write_text(EQ_TOP.format(ports=ports, conns=conns))
    (prj / "eq_reset.ys").write_text(EQ_RESET.format(module=module, prj=prj))
    return True


def run_task(prj, script, name, mutation, env=None):
    """One test script on one mutation in tasks/<name>/, as mcy runs it: (idx, PASS|FAIL)."""
    task = prj / "tasks" / name
    shutil.rmtree(task, ignore_errors=True)
    task.mkdir(parents=True)
    (task / "input.txt").write_text(f"{mutation}\n")
    env = os.environ | {"PRJDIR": str(prj), "MUTATIONS": name} | (env or {})
    subprocess.run(["bash", str(prj / script)], cwd=task, check=True, env=env, stdout=subprocess.DEVNULL)
    result = (task / "output.txt").read_text().split()
    return task, result[1]


def selftest_eq(prj, jobs):
    """The proof from reset must hold for the original against itself and fail for some
    mutant: a broken model (say, a vacuous assumption, or a mask that hides differences)
    would otherwise make every mutant equivalent, and no survivors look like a perfect score."""
    only = {"EQ_RESET_ONLY": "1"}
    task, result = run_task(prj, "test_eq.sh", "selftest-eq", "1 mutate -mode none", only)
    if result != "PASS":
        sys.exit(f"harness self-test: the original isn't equivalent to itself from reset; see {task}")
    shutil.rmtree(task)
    db = sqlite3.connect(prj / "database" / "db.sqlite3")
    mutations = db.execute("SELECT mutation_id, mutation FROM mutations ORDER BY mutation_id").fetchall()

    def check(item):  # a short timeout: a quick counterexample is what's wanted here
        idx, mutation = item
        task, result = run_task(
            prj, "test_eq.sh", f"selftest-eq-{idx}", f"{idx} {mutation}", only | {"EQ_TIMEOUT": "30"}
        )
        shutil.rmtree(task)
        return idx, result

    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(check, mutations[1 : EQ_SELFTEST_TRIES + 1]))  # 1 is mcy's no-op
    failed = [idx for idx, result in results if result == "FAIL"]
    if not failed:
        sys.exit(f"harness self-test: none of the first {EQ_SELFTEST_TRIES} mutants is non-equivalent from reset")
    print(f"[INFO] Equivalence self-test OK (mutant {failed[0]} is not equivalent from reset)")


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


def describe(mutation):
    """A mutant as "<mode> <port>[<bit>] ... @ <file>:<line>.<col>-...": what it does and
    where, without the cell names, which change with any RTL edit."""
    opts = dict(re.findall(r"-(mode|port|portbit|ctrlbit|wire|wirebit) (\S+)", mutation))
    srcs = sorted(Path(src).name for src in re.findall(r"-src (\S+)", mutation))
    if not srcs:  # a cell Yosys made (proc_dff's reset-hold mux): its name without the numbers
        srcs = [re.sub(r"[:$]\d+", "", cell) for cell in re.findall(r"-cell (\S+)", mutation)]
    what = [opts.get("mode", "?")]
    if "port" in opts:
        what.append(f"{opts['port']}[{opts.get('portbit', '?')}]")
    if "ctrlbit" in opts:
        what.append(f"ctrl {opts['ctrlbit']}")
    if "wire" in opts:
        what.append(f"{opts['wire']}[{opts.get('wirebit', '?')}]")
    return " ".join(what) + " @ " + " ".join(srcs)


def summarize(module, prj):
    db = sqlite3.connect(prj / "database" / "db.sqlite3")
    tags = dict(db.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag").fetchall())
    survivors = db.execute(
        "SELECT m.mutation_id, m.mutation FROM mutations m "
        "JOIN tags t ON t.mutation_id = m.mutation_id AND t.tag = 'SURVIVED' ORDER BY m.mutation_id"
    ).fetchall()
    detected, survived, equivalent = (tags.get(t, 0) for t in ("DETECTED", "SURVIVED", "EQUIVALENT"))
    accepted = ACCEPTED.get(module, {})
    explained = [(i, describe(mut), mut) for i, mut in survivors if describe(mut) in accepted]
    open_ = [(i, describe(mut), mut) for i, mut in survivors if describe(mut) not in accepted]
    n = detected + survived
    lines = [
        f"# Mutation testing: {module}",
        "",
        f"Detected {detected} of {n} non-equivalent mutants ({100 * detected / max(n, 1):.1f}%); "
        f"{equivalent} equivalent (left out). Survivors: {len(open_)} open, {len(explained)} accepted.",
        "",
    ]
    if module in NO_EQUIV:
        lines += [f"No equivalence check ({NO_EQUIV[module]}): a survivor may be an equivalent mutant.", ""]
    if open_:
        lines += ["## Open survivors (add a test, or an ACCEPTED entry with the reason)", ""]
        lines += ["| id | mutant | mutation |", "|---|---|---|"]
        lines += [f"| {i} | {d} | `{mut}` |" for i, d, mut in open_]
        lines += [""]
    if explained:
        lines += ["## Accepted survivors (scripts/mcy/mutation.py's ACCEPTED)", ""]
        lines += ["| id | mutant | why it is harmless |", "|---|---|---|"]
        lines += [f"| {i} | {d} | {accepted[d]} |" for i, d, _ in explained]
        lines += [""]
    seen = {d for _, d, _ in explained}
    stale = [d for d in accepted if d not in seen]
    if stale:
        # Not a survivor this run: detected now, or the RTL moved (a new line or cell)
        lines += ["ACCEPTED entries no survivor matched: " + "; ".join(stale), ""]
    (prj / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


def replay(module, ids, nodes):
    """The unit tests against single mutants of the last run, kept for debugging."""
    prj = MCY_DIR / module
    db_file = prj / "database" / "db.sqlite3"
    if not db_file.exists():
        sys.exit(f"{module}: no mutation project in {prj}; run `make mutation MCY_TOP={module}` first")
    db = sqlite3.connect(db_file)
    env = {"MUT_NODES": " ".join(nodes)} if nodes else {}
    for idx in ids:
        row = db.execute("SELECT mutation FROM mutations WHERE mutation_id = ?", (idx,)).fetchone()
        if not row:
            sys.exit(f"{module}: no mutant {idx}")
        task, result = run_task(prj, "test_sim.sh", f"replay-{idx}", f"{idx} {row[0]}", env)
        out = prj / "replay" / str(idx)
        shutil.rmtree(out, ignore_errors=True)
        out.parent.mkdir(exist_ok=True)
        shutil.move(task, out)
        verdict = "survives (tests pass)" if result == "PASS" else "detected (tests fail)"
        print(f"{module} mutant {idx}: {verdict}; {out}/sim.out, {out}/mutated.v")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="+", help=f"modules, or 'all' ({', '.join(TARGETS)})")
    ap.add_argument("--size", type=int, default=100, help="mutants per module")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--replay", type=int, nargs="+", metavar="ID", help="rerun the tests on single mutants")
    ap.add_argument("--node", action="append", help="with --replay: pytest node(s) instead of the module's unit tests")
    ap.add_argument("--summarize", action="store_true", help="only rewrite the summaries of the last run")
    args = ap.parse_args()
    if args.summarize:
        for module in args.modules:
            summarize(module, MCY_DIR / module)
        return
    if args.replay:
        if len(args.modules) != 1:
            sys.exit("--replay takes one module")
        replay(args.modules[0], args.replay, args.node)
        return
    modules = list(TARGETS) if args.modules == ["all"] else args.modules
    for module in modules:
        if module not in TARGETS:
            sys.exit(f"{module}: not a mutation target ({', '.join(TARGETS)})")
    for module in modules:
        jobs = 1 if module == "unified_cpu" else args.jobs
        prj = setup(module, args.size, args.seed, max(jobs, args.jobs))
        subprocess.run(["mcy", "init"], cwd=prj, check=True)
        selftest(prj)
        if module not in NO_EQUIV and write_eq_reset(prj, module):
            selftest_eq(prj, args.jobs)
        subprocess.run(["mcy", "run", "-j", str(jobs)], cwd=prj, check=True)
        summarize(module, prj)


if __name__ == "__main__":
    main()
