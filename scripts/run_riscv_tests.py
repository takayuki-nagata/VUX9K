#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""RV32I ISA test harness: riscv-tests (rv32ui/rv32mi, env/p) on sim/tb_hex_runner.veryl.

Each test is self-checking: riscv-tests' env/p writes 1 to `tohost` on PASS, or
(failing TESTNUM << 1) | 1 on FAIL. scripts/riscv_tests/link.ld places `tohost` at
the testbench's TOHOST_ADDR, where sim/tb_hex_runner.veryl turns the write into a
[PASS]/[FAIL] line and exit code.

Before the suite runs, a deliberately failing test (scripts/riscv_tests/selftest_fail.S)
must be reported as FAIL with TESTNUM=2, otherwise the harness itself is broken and the
run aborts.

Tests listed in EXPECTED_FAILURES document known CPU gaps. They must keep failing: an
unexpected pass is reported as XPASS and fails the run, so the list can't go stale.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_DIR, "vendor")
RISCV_TESTS_DIR = os.path.join(VENDOR_DIR, "riscv-tests")
BUILD_DIR = os.path.join(REPO_DIR, "build", "riscv_tests")
VERYL_OUT_DIR = os.path.join(REPO_DIR, "build", "veryl")
HARNESS_DIR = os.path.join(REPO_DIR, "scripts", "riscv_tests")

RISCV_TESTS_REPO = "https://github.com/riscv-software-src/riscv-tests.git"
RISCV_TESTS_COMMIT = "793a5ff2d99a6d9fbd91e84c34b9a0437e313b88"
# riscv-tests' `env` submodule, pinned to the gitlink recorded at RISCV_TESTS_COMMIT
RISCV_TEST_ENV_REPO = "https://github.com/riscv/riscv-test-env.git"
RISCV_TEST_ENV_COMMIT = "6de71edb142be36319e380ce782c3d1830c65d68"

SUITES = ("rv32ui", "rv32mi")
# Every rv32ui test finishes in well under 100k cycles; the cap only bounds hangs.
MAX_CYCLES = 200000

# Known CPU gaps, "<suite>-p-<test>": reason. Keep reasons specific enough to act on.
EXPECTED_FAILURES: dict[str, str] = {
    "rv32ui-p-ma_data": "misaligned load/store traps instead of being handled in hardware; "
    "env/p has no handler to emulate them",
    "rv32mi-p-pmpaddr": "PMP CSRs (pmpcfg0/pmpaddr0) not implemented",
}

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
IVERILOG_BIN = shutil.which("iverilog")
VVP_BIN = shutil.which("vvp")


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
            ("iverilog", IVERILOG_BIN),
            ("vvp", VVP_BIN),
        )
        if not p
    ]
    if missing:
        sys.exit(f"[ERROR] Missing tools: {', '.join(missing)}")


def compile_testbench():
    code, _, err = run_cmd(["veryl", "build", "--out-dir", VERYL_OUT_DIR], cwd=REPO_DIR)
    if code != 0:
        sys.exit(f"[ERROR] veryl build failed: {err}")
    cpu_dir = os.path.join(VERYL_OUT_DIR, "soc", "cpu")
    sv_files = [
        os.path.join(cpu_dir, f"{m}.sv")
        for m in (
            "rv32i_pkg",
            "rv32i_alu",
            "rv32i_decode",
            "rv32i_regfile",
            "rv32i_csrs",
            "hack_translator",
            "auto_mode_detector",
            "unified_cpu",
        )
    ]
    sv_files.append(os.path.join(VERYL_OUT_DIR, "sim", "tb_hex_runner.sv"))
    vvp = os.path.join(BUILD_DIR, "tb_hex_runner.vvp")
    code, _, err = run_cmd([IVERILOG_BIN, "-g2012", "-o", vvp] + sv_files)
    if code != 0:
        sys.exit(f"[ERROR] Failed to compile tb_hex_runner: {err}")
    return vvp


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
    with open(binf, "rb") as f:
        data = f.read()
    data += b"\x00" * (-len(data) % 4)
    with open(hexf, "w") as f:
        for i in range(0, len(data), 4):
            f.write(f"{int.from_bytes(data[i : i + 4], 'little'):08X}\n")
    return hexf, None


def simulate(vvp, hexf):
    """Returns (passed, detail line)."""
    cmd = [VVP_BIN, "-n", vvp, f"+HEX_FILE={hexf}", f"+MAX_CYCLES={MAX_CYCLES}"]
    code, out, err = run_cmd(cmd, cwd=BUILD_DIR)
    lines = [ln for ln in out.splitlines() if ln.startswith(("[PASS]", "[FAIL]"))]
    detail = lines[-1] if lines else (out + err).strip()
    return code == 0 and bool(lines) and lines[-1].startswith("[PASS]"), detail


def run_selftest(vvp):
    hexf, err = build_hex(os.path.join(HARNESS_DIR, "selftest_fail.S"), "selftest_fail")
    if err:
        sys.exit(f"[ERROR] Self-test build failed: {err}")
    passed, detail = simulate(vvp, hexf)
    if passed or "(TESTNUM=2)" not in detail:
        sys.exit(f"[ERROR] Harness self-test: expected FAIL with TESTNUM=2, got: {detail}")
    print(f"[INFO] Harness self-test OK (deliberate failure detected: {detail})")


def run_suite(vvp, only=None):
    counts = {"PASS": 0, "FAIL": 0, "XFAIL": 0, "XPASS": 0}
    for suite in SUITES:
        for test in list_tests(suite):
            name = f"{suite}-p-{test}"
            if only and name not in only:
                continue
            src = os.path.join(RISCV_TESTS_DIR, "isa", suite, f"{test}.S")
            hexf, err = build_hex(src, name)
            passed, detail = (False, err) if err else simulate(vvp, hexf)
            expected_fail = name in EXPECTED_FAILURES
            if passed and expected_fail:
                status = "XPASS"
                detail = f"listed in EXPECTED_FAILURES ({EXPECTED_FAILURES[name]}) but passed"
            elif passed:
                status = "PASS"
            elif expected_fail:
                status = "XFAIL"
                detail = f"{EXPECTED_FAILURES[name]} | {detail}"
            else:
                status = "FAIL"
            counts[status] += 1
            print(f"[{status}] {name}" + ("" if status == "PASS" else f": {detail}"))

    stale = [
        n
        for n in EXPECTED_FAILURES
        if only is None and not any(n == f"{s}-p-{t}" for s in SUITES for t in list_tests(s))
    ]
    for name in stale:
        print(f"[ERROR] EXPECTED_FAILURES entry {name} does not match any test")

    total = sum(counts.values())
    print("\n" + "=" * 60)
    print("riscv-tests results: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + f" (total {total})")
    print("=" * 60)
    return total > 0 and counts["FAIL"] == 0 and counts["XPASS"] == 0 and not stale


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tests", nargs="*", help="run only these tests, e.g. rv32ui-p-add")
    args = parser.parse_args()

    check_tools()
    os.makedirs(BUILD_DIR, exist_ok=True)
    setup_sources()
    vvp = compile_testbench()
    run_selftest(vvp)
    if not run_suite(vvp, only=set(args.tests) or None):
        sys.exit(1)


if __name__ == "__main__":
    main()
