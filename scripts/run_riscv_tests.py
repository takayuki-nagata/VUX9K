#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""RV32I ISA test harness: riscv-tests (rv32ui/rv32mi, env/p) on sim/tb_hex_runner.veryl.

Each test is self-checking: riscv-tests' env/p writes 1 to `tohost` on PASS, or
(failing TESTNUM << 1) | 1 on FAIL. scripts/riscv_tests/link.ld places `tohost` at
the testbench's TOHOST_ADDR, where sim/tb_hex_runner.veryl latches the write and the
cocotb test scripts/riscv_tests/hex_runner.py turns it into a [PASS]/[FAIL] line.
The testbench is compiled once (build/sim/<sim>[-gls]/tb_hex_runner/; the simulator
is $SIM, default icarus); each test runs in build/riscv_tests/runs[-gls]/<test>/, which
holds its program.hex, verdict.txt and sim.log (runs-cov/ for `make coverage`). Tests run
in parallel (--jobs).

`--gls` runs them on the gate-level unified_cpu netlist (build/synth/unified_cpu_syn.v,
`make synth-units`) instead of the RTL: the testbench's memory stays RTL. This catches
constructs that Yosys reads differently from the simulators (docs/agents/veryl.md, "Veryl constructs
the toolchain rejects"), which the RTL runs and `make eqy` can't see.

`--backend emu` runs the same tests on the Rust emulator instead (emu/, the isa-test
profile: the same flat 256 KB RAM and tohost), through its Python module
(build/emu/python, `make emu-py`; VUX9K_EMU_PY_DIR overrides it). The verdicts and
EXPECTED_FAILURES must match.

Before the suite runs, a deliberately failing test (scripts/riscv_tests/selftest_fail.S)
must be reported as FAIL with TESTNUM=2, otherwise the harness itself is broken and the
run aborts.

Tests listed in EXPECTED_FAILURES document known CPU gaps. They must keep failing: an
unexpected pass is reported as XPASS and fails the run, so the list can't go stale.

`--suite act4` runs riscv-arch-test's ACT4 tests instead (`make act4-elfs`,
scripts/act4/): self-checking ELFs with the Sail reference model's results built in,
which stop through the same `tohost` (1 = PASS, 3 = FAIL) and print their failure details
to the testbench's console word (console.txt in the run directory). Their harness
self-test is scripts/act4/selftest_fail.S; their known gaps are EXPECTED_FAILURES_ACT4.
"""

import argparse
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_DIR, "vendor")
RISCV_TESTS_DIR = os.path.join(VENDOR_DIR, "riscv-tests")
BUILD_DIR = os.path.join(REPO_DIR, "build", "riscv_tests")
VERYL_OUT_DIR = os.path.join(REPO_DIR, "build", "veryl")
HARNESS_DIR = os.path.join(REPO_DIR, "scripts", "riscv_tests")

# The cocotb test module (hex_runner.py) is imported by name inside the simulator,
# from the PYTHONPATH sim_runner builds out of this process's sys.path
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, os.path.join(REPO_DIR, "sim", "runners"))
import sim_runner  # noqa: E402

sys.path.insert(0, os.path.join(REPO_DIR, "scripts", "act4"))
import act4_elfs  # noqa: E402

RISCV_TESTS_REPO = "https://github.com/riscv-software-src/riscv-tests.git"
RISCV_TESTS_COMMIT = "793a5ff2d99a6d9fbd91e84c34b9a0437e313b88"
# riscv-tests' `env` submodule, pinned to the gitlink recorded at RISCV_TESTS_COMMIT
RISCV_TEST_ENV_REPO = "https://github.com/riscv/riscv-test-env.git"
RISCV_TEST_ENV_COMMIT = "6de71edb142be36319e380ce782c3d1830c65d68"

SUITES = ("rv32ui", "rv32mi")
# Every rv32ui test finishes in well under 100k cycles; the cap only bounds hangs.
# The ACT4 tests are longer (a branch test checks 400+ cases); main() sets their cap.
MAX_CYCLES = 200000
MAX_CYCLES_ACT4 = 5000000
# sim/tb_hex_runner.veryl's RAM (256 KB); the image must fit
RAM_WORDS = 65536

# Known CPU gaps, "<suite>-p-<test>": reason. Keep reasons specific enough to act on.
EXPECTED_FAILURES: dict[str, str] = {
    "rv32ui-p-ma_data": "misaligned load/store traps instead of being handled in hardware; "
    "env/p has no handler to emulate them",
    "rv32mi-p-pmpaddr": "PMP CSRs (pmpcfg0/pmpaddr0) not implemented",
}

# The same for the ACT4 tests, by ELF path below the elfs/ directory. Each is a difference
# between the Sail configuration the expected values come from and this harness/CPU.
_MTIP = (
    "Sail's CLINT (required by the generator) starts with mtimecmp = 0, so its mip.MTIP is set; "
    "tb_hex_runner raises no timer interrupt, so mip reads 0"
)
EXPECTED_FAILURES_ACT4: dict[str, str] = {
    "priv/InterruptsSm/InterruptsSm-00.elf": "tb_hex_runner has no interrupt sources "
    "(RVMODEL_SET_*_INT are empty), so the interrupts the test raises never arrive",
    "priv/Sm/Sm_mcsr_access-00.elf": _MTIP,
    "priv/Sm/Sm_mcsr_walk-02.elf": _MTIP,
    "priv/Sm/Sm_mcsr_cntr-00.elf": "mcountinhibit is read-only zero (legal WARL: counters never "
    "inhibited), but Sail 0.13.1 keeps its CY/IR bits writable and expects mcycle to stop",
}
ACT4_HARNESS_DIR = os.path.join(REPO_DIR, "scripts", "act4")

ZEPHYR_SDK_BIN = os.path.expanduser("~/.local/zephyr-sdk-0.16.8/riscv64-zephyr-elf/bin")
TOOL_PREFIXES = ("riscv64-zephyr-elf-", "riscv64-unknown-elf-", "riscv32-unknown-elf-", "riscv64-linux-gnu-")


def find_tool(name):
    for prefix in TOOL_PREFIXES:
        path = shutil.which(prefix + name) or shutil.which(os.path.join(ZEPHYR_SDK_BIN, prefix + name))
        if path:
            return path
    return None


GCC_BIN = find_tool("gcc")
OBJCOPY_BIN = find_tool("objcopy")


def run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr


def fetch_pinned(repo_dir, url, commit):
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        code, head, _ = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        if code == 0 and head.strip() == commit:
            return
    print(f"[INFO] Fetching {url} @ {commit[:8]} into {os.path.relpath(repo_dir, REPO_DIR)}")
    os.makedirs(repo_dir, exist_ok=True)
    steps = [
        ["git", "init", "-q"],
        ["git", "fetch", "-q", "--depth", "1", url, commit],
        ["git", "checkout", "-q", "FETCH_HEAD"],
    ]
    for cmd in steps:
        code, _, err = run_cmd(cmd, cwd=repo_dir)
        if code != 0:
            sys.exit(f"[ERROR] {' '.join(cmd)} failed in {repo_dir}: {err}")


def setup_sources():
    fetch_pinned(RISCV_TESTS_DIR, RISCV_TESTS_REPO, RISCV_TESTS_COMMIT)
    fetch_pinned(os.path.join(RISCV_TESTS_DIR, "env"), RISCV_TEST_ENV_REPO, RISCV_TEST_ENV_COMMIT)


def check_tools():
    missing = [
        n
        for n, p in (
            ("RISC-V gcc", GCC_BIN),
            ("RISC-V objcopy", OBJCOPY_BIN),
        )
        if not p
    ]
    if missing:
        sys.exit(f"[ERROR] Missing tools: {', '.join(missing)}")


def build_veryl():
    code, _, err = run_cmd(["veryl", "build", "--out-dir", VERYL_OUT_DIR], cwd=REPO_DIR)
    if code != 0:
        sys.exit(f"[ERROR] veryl build failed: {err}")


def list_tests(suite):
    """Parse `<suite>_sc_tests = ...` from riscv-tests' Makefrag (the upstream test list)."""
    with open(os.path.join(RISCV_TESTS_DIR, "isa", suite, "Makefrag")) as f:
        text = f.read()
    m = re.search(rf"^{suite}_sc_tests\s*=\s*((?:.*\\\n)*.*)$", text, re.MULTILINE)
    if not m:
        sys.exit(f"[ERROR] Could not find {suite}_sc_tests in Makefrag")
    return m.group(1).replace("\\\n", " ").split()


def build_hex(src, name):
    elf = os.path.join(BUILD_DIR, f"{name}.elf")
    binf = os.path.join(BUILD_DIR, f"{name}.bin")
    hexf = os.path.join(BUILD_DIR, f"{name}.hex")
    cmd = [
        GCC_BIN,
        "-march=rv32i_zicsr_zifencei",
        "-mabi=ilp32",
        "-static",
        "-mcmodel=medany",
        "-fvisibility=hidden",
        "-nostdlib",
        "-nostartfiles",
        "-I",
        os.path.join(RISCV_TESTS_DIR, "env", "p"),
        "-I",
        os.path.join(RISCV_TESTS_DIR, "isa", "macros", "scalar"),
        "-T",
        os.path.join(HARNESS_DIR, "link.ld"),
        src,
        "-o",
        elf,
    ]
    code, _, err = run_cmd(cmd)
    if code != 0:
        return None, f"compile failed: {err.strip()}"
    code, _, err = run_cmd([OBJCOPY_BIN, "-O", "binary", elf, binf])
    if code != 0:
        return None, f"objcopy failed: {err.strip()}"
    return bin_to_hex(binf, hexf)


def bin_to_hex(binf, hexf):
    with open(binf, "rb") as f:
        data = f.read()
    data += b"\x00" * (-len(data) % 4)
    if len(data) > RAM_WORDS * 4:
        return None, f"image is {len(data)} bytes, larger than the testbench's {RAM_WORDS * 4}-byte RAM"
    with open(hexf, "w") as f:
        for i in range(0, len(data), 4):
            f.write(f"{int.from_bytes(data[i : i + 4], 'little'):08X}\n")
    return hexf, None


def act4_hex(elf, name):
    """program.hex of an ACT4 ELF, from address 0 (the jump to TEST_BASE) and with .bss
    zero-filled, as an ELF loader would leave memory."""
    binf = os.path.join(BUILD_DIR, "act4", f"{name}.bin")
    os.makedirs(os.path.dirname(binf), exist_ok=True)
    cmd = [OBJCOPY_BIN, "-O", "binary", "--set-section-flags", ".bss=alloc,load,contents", elf, binf]
    code, _, err = run_cmd(cmd)
    if code != 0:
        return None, f"objcopy failed: {err.strip()}"
    return bin_to_hex(binf, binf.removesuffix(".bin") + ".hex")


def build_act4_selftest():
    src = os.path.join(ACT4_HARNESS_DIR, "selftest_fail.S")
    config = os.path.join(ACT4_HARNESS_DIR, "vux9k")
    elf = os.path.join(BUILD_DIR, "act4", "selftest_fail.elf")
    os.makedirs(os.path.dirname(elf), exist_ok=True)
    cmd = [GCC_BIN, "-march=rv32i_zicsr_zifencei", "-mabi=ilp32", "-static", "-nostdlib", "-nostartfiles"]
    cmd += ["-I", config, "-T", os.path.join(config, "link.ld"), src, "-o", elf]
    code, _, err = run_cmd(cmd)
    if code != 0:
        sys.exit(f"[ERROR] ACT4 self-test build failed: {err.strip()}")
    return act4_hex(elf, "selftest_fail")


# Set from the command line in main(); passed on to the workers explicitly
BACKEND = "rtl"
GLS = False
SUITE = "riscv-tests"


def simulate(hexf, backend, gls):
    """Returns (passed, detail line) from the selected backend."""
    return simulate_emu(hexf) if backend == "emu" else simulate_rtl(hexf, gls)


def simulate_emu(hexf):
    """Run on the Rust emulator's isa-test profile (tb_hex_runner's memory and tohost)."""
    sys.path.insert(0, os.environ.get("VUX9K_EMU_PY_DIR") or os.path.join(REPO_DIR, "build", "emu", "python"))
    import vux9k_emu  # noqa: PLC0415 (built by `make emu-py`; only this backend needs it)

    with open(hexf) as f:
        image = b"".join(int(line, 16).to_bytes(4, "little") for line in f if line.strip())
    soc = vux9k_emu.Soc("isa-test")
    soc.load_iram(0, image)
    stop, code = soc.run(MAX_CYCLES)
    if stop != "tohost":
        return False, f"[FAIL] Simulation timeout after {MAX_CYCLES} cycles at PC={soc.pc:08x}"
    if code == 1:
        return True, f"[PASS] tohost=1 at cycle {soc.cycle}"
    return False, f"[FAIL] tohost=0x{code:08x} (TESTNUM={code >> 1}) at cycle {soc.cycle}"


def simulate_rtl(hexf, gls):
    """Returns (passed, detail line). The first call also compiles the testbench."""
    name = os.path.basename(hexf).removesuffix(".hex")
    # HDL_COVERAGE=1 (make coverage): a run directory of its own, holding coverage.dat
    runs = "runs-gls" if gls else "runs-cov" if os.environ.get("HDL_COVERAGE", "") not in ("", "0") else "runs"
    runs = runs.replace("runs", "runs-act4") if SUITE == "act4" else runs
    # A mutant's runs (make mutation: VUX9K_RTL_OVERRIDE, see sim_runner) stay in its own directory
    base = Path(os.environ["VUX9K_MUT_BUILD_DIR"]) if os.environ.get("VUX9K_RTL_OVERRIDE") else Path(BUILD_DIR)
    test_dir = base / runs / name
    test_dir.mkdir(parents=True, exist_ok=True)
    verdict = test_dir / "verdict.txt"
    verdict.unlink(missing_ok=True)
    program = test_dir / "program.hex"
    program.unlink(missing_ok=True)
    program.symlink_to(os.path.abspath(hexf))
    log = test_dir / "sim.log"
    try:
        with open(log, "w") as f:
            # The simulator's output goes to the log, not our stdout (it's noisy)
            saved = os.dup(1), os.dup(2)
            os.dup2(f.fileno(), 1)
            os.dup2(f.fileno(), 2)
            try:
                sim_runner.run(
                    "tb_hex_runner",
                    "hex_runner",
                    gls=gls,
                    test_dir=test_dir,
                    extra_env={"MAX_CYCLES": str(MAX_CYCLES)},
                )
            finally:
                os.dup2(saved[0], 1)
                os.dup2(saved[1], 2)
    except BaseException as e:  # the cocotb runner raises SystemExit on some failures
        return False, f"harness error ({e!r}); see {os.path.relpath(log, REPO_DIR)}"
    if not verdict.exists():
        return False, f"no verdict; see {os.path.relpath(log, REPO_DIR)}"
    detail = verdict.read_text().strip()
    console = test_dir / "console.txt"
    if not detail.startswith("[PASS]") and console.exists():
        printed = console.read_text(errors="replace").strip().replace("\n", " / ")
        detail += f" | console: {printed[-300:]}"
    return detail.startswith("[PASS]"), detail


def run_selftest():
    hexf, err = build_hex(os.path.join(HARNESS_DIR, "selftest_fail.S"), "selftest_fail")
    if err:
        sys.exit(f"[ERROR] Self-test build failed: {err}")
    passed, detail = simulate(hexf, BACKEND, GLS)
    if passed or "(TESTNUM=2)" not in detail:
        sys.exit(f"[ERROR] Harness self-test: expected FAIL with TESTNUM=2, got: {detail}")
    print(f"[INFO] Harness self-test OK (deliberate failure detected: {detail})")


def run_one(suite, test, backend, gls):
    """Build and run one test; returns (passed, detail). Runs in a worker process."""
    name = f"{suite}-p-{test}"
    src = os.path.join(RISCV_TESTS_DIR, "isa", suite, f"{test}.S")
    hexf, err = build_hex(src, name)
    return (False, err) if err else simulate(hexf, backend, gls)


def run_act4_selftest():
    hexf, err = build_act4_selftest()
    if err:
        sys.exit(f"[ERROR] ACT4 self-test: {err}")
    passed, detail = simulate(hexf, BACKEND, GLS)
    console_ok = BACKEND == "emu" or "act4 selftest" in detail  # the emulator has no console
    if passed or "(TESTNUM=1)" not in detail or not console_ok:
        sys.exit(f"[ERROR] ACT4 harness self-test: expected FAIL with TESTNUM=1 and its console line, got: {detail}")
    print(f"[INFO] ACT4 harness self-test OK (deliberate failure detected: {detail})")


def run_act4_one(name, elf, backend, gls):
    hexf, err = act4_hex(elf, name.replace("/", "__").removesuffix(".elf"))
    return (False, err) if err else simulate(hexf, backend, gls)


def run_parallel(func, args, jobs):
    """func(*a, BACKEND, GLS) for each a, in worker processes; results in order. Each test has
    its own run directory, so they can run concurrently (the testbench is already compiled
    by the self-test). Workers are forked: simulate_rtl redirects fds 1/2, which must stay
    per process."""
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(max(1, jobs)) as pool:
        return pool.starmap(func, [(*a, BACKEND, GLS) for a in args], chunksize=1)


def report(label, names, results, expected, all_names):
    """Print each result against the expected failures; True if the run passes."""
    counts = {"PASS": 0, "FAIL": 0, "XFAIL": 0, "XPASS": 0}
    for name, (passed, detail) in zip(names, results, strict=True):
        expected_fail = name in expected
        if passed and expected_fail:
            status = "XPASS"
            detail = f"listed as an expected failure ({expected[name]}) but passed"
        elif passed:
            status = "PASS"
        elif expected_fail:
            status = "XFAIL"
            detail = f"{expected[name]} | {detail}"
        else:
            status = "FAIL"
        counts[status] += 1
        print(f"[{status}] {name}" + ("" if status == "PASS" else f": {detail}"))

    stale = [n for n in expected if all_names is not None and n not in all_names]
    for name in stale:
        print(f"[ERROR] expected-failure entry {name} does not match any test")

    total = sum(counts.values())
    print("\n" + "=" * 60)
    print(f"{label} results: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + f" (total {total})")
    print("=" * 60)
    return total > 0 and counts["FAIL"] == 0 and counts["XPASS"] == 0 and not stale


def run_suite(only=None, jobs=1):
    tests = [(s, t) for s in SUITES for t in list_tests(s) if not only or f"{s}-p-{t}" in only]
    results = run_parallel(run_one, tests, jobs)
    all_names = None if only else {f"{s}-p-{t}" for s in SUITES for t in list_tests(s)}
    return report("riscv-tests", [f"{s}-p-{t}" for s, t in tests], results, EXPECTED_FAILURES, all_names)


def run_act4_suite(only=None, jobs=1):
    root = act4_elfs.out_dir() / "elfs"
    every = {str(p.relative_to(root)): p for p in act4_elfs.elfs()}
    if not every:
        sys.exit(f"[ERROR] No ACT4 ELFs in {os.path.relpath(root, REPO_DIR)}: run `make act4-elfs` first")
    names = [n for n in every if not only or n in only]
    results = run_parallel(run_act4_one, [(n, str(every[n])) for n in names], jobs)
    return report("ACT4", names, results, EXPECTED_FAILURES_ACT4, None if only else set(every))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tests", nargs="*", help="run only these tests, e.g. rv32ui-p-add")
    parser.add_argument("--backend", choices=("rtl", "emu"), default="rtl", help="RTL (cocotb) or the Rust emulator")
    parser.add_argument("--gls", action="store_true", help="gate-level unified_cpu netlist (make synth-units)")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count(), help="parallel tests (default: CPUs)")
    parser.add_argument("--suite", choices=("riscv-tests", "act4"), default="riscv-tests")
    args = parser.parse_args()
    global BACKEND, GLS, SUITE, MAX_CYCLES
    BACKEND = args.backend
    GLS = args.gls
    SUITE = args.suite
    if SUITE == "act4":
        MAX_CYCLES = MAX_CYCLES_ACT4
    if GLS and BACKEND != "rtl":
        parser.error("--gls needs --backend rtl")

    check_tools()
    os.makedirs(BUILD_DIR, exist_ok=True)
    setup_sources()
    if BACKEND == "rtl":
        build_veryl()  # also the testbench, which GLS runs keep as RTL
    if SUITE == "act4":
        run_act4_selftest()
        ok = run_act4_suite(only=set(args.tests) or None, jobs=args.jobs)
    else:
        run_selftest()
        ok = run_suite(only=set(args.tests) or None, jobs=args.jobs)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
