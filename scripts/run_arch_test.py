#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# RISC-V Architectural Compliance Test Automation Harness (Icarus / SystemVerilog)

import os
import sys
import subprocess
import shutil
import glob

# Tool discovery
ZEPHYR_SDK_GCC = os.path.expanduser("~/.local/zephyr-sdk-0.16.8/riscv64-zephyr-elf/bin/riscv64-zephyr-elf-gcc")
ZEPHYR_SDK_OBJCOPY = os.path.expanduser("~/.local/zephyr-sdk-0.16.8/riscv64-zephyr-elf/bin/riscv64-zephyr-elf-objcopy")

GCC_BIN = (
    shutil.which("riscv64-zephyr-elf-gcc")
    or shutil.which("riscv64-linux-gnu-gcc")
    or shutil.which("riscv64-unknown-elf-gcc")
    or shutil.which("riscv32-unknown-elf-gcc")
    or (ZEPHYR_SDK_GCC if os.path.exists(ZEPHYR_SDK_GCC) else None)
    or shutil.which("gcc")
)
OBJCOPY_BIN = (
    shutil.which("riscv64-zephyr-elf-objcopy")
    or shutil.which("riscv64-linux-gnu-objcopy")
    or shutil.which("riscv64-unknown-elf-objcopy")
    or (ZEPHYR_SDK_OBJCOPY if os.path.exists(ZEPHYR_SDK_OBJCOPY) else None)
    or shutil.which("objcopy")
)
IVERILOG_BIN = shutil.which("iverilog")
VVP_BIN = shutil.which("vvp")

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_DIR, "vendor")
ARCH_TEST_DIR = os.path.join(VENDOR_DIR, "riscv-arch-test")
BUILD_DIR = os.path.join(REPO_DIR, "build_arch_test")
TARGET_ENV_DIR = os.path.join(REPO_DIR, "scripts", "target_env")

ARCH_TEST_REPO = "https://github.com/riscv-non-isa/riscv-arch-test.git"
ARCH_TEST_COMMIT = "74efcaac81f48f437f58868771daf2ed2776d422"

def run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr

def setup_repo():
    os.makedirs(VENDOR_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)
    
    test_src = os.path.join(ARCH_TEST_DIR, "tests", "rv32i", "I")
    if not os.path.exists(test_src) or not os.listdir(test_src):
        print(f"[INFO] Fetching riscv-arch-test repository (commit {ARCH_TEST_COMMIT[:8]})...")
        os.makedirs(ARCH_TEST_DIR, exist_ok=True)
        code, out, err = run_cmd(["git", "init"], cwd=ARCH_TEST_DIR)
        if code != 0:
            print(f"[ERROR] Failed to init riscv-arch-test git: {err}")
            sys.exit(1)
        run_cmd(["git", "remote", "add", "origin", ARCH_TEST_REPO], cwd=ARCH_TEST_DIR)
        code, out, err = run_cmd(["git", "fetch", "--depth", "1", "origin", ARCH_TEST_COMMIT], cwd=ARCH_TEST_DIR)
        if code != 0:
            print(f"[ERROR] Failed to fetch riscv-arch-test commit {ARCH_TEST_COMMIT}: {err}")
            sys.exit(1)
        code, out, err = run_cmd(["git", "checkout", "FETCH_HEAD"], cwd=ARCH_TEST_DIR)
        if code != 0:
            print(f"[ERROR] Failed to checkout riscv-arch-test: {err}")
            sys.exit(1)

def compile_verilog():
    print("[INFO] Building Veryl sources...")
    run_cmd(["veryl", "build"], cwd=REPO_DIR)
    print("[INFO] Compiling SystemVerilog entities with Icarus Verilog...")
    sv_files = [
        os.path.join(REPO_DIR, "cpu", "rv32i_pkg.sv"),
        os.path.join(REPO_DIR, "cpu", "rv32i_alu.sv"),
        os.path.join(REPO_DIR, "cpu", "rv32i_decode.sv"),
        os.path.join(REPO_DIR, "cpu", "rv32i_regfile.sv"),
        os.path.join(REPO_DIR, "cpu", "rv32i_csrs.sv"),
        os.path.join(REPO_DIR, "cpu", "hack_translator.sv"),
        os.path.join(REPO_DIR, "cpu", "auto_mode_detector.sv"),
        os.path.join(REPO_DIR, "cpu", "unified_cpu.sv"),
        os.path.join(REPO_DIR, "sim", "tb_hex_runner.sv"),
    ]
    out_vvp = os.path.join(BUILD_DIR, "sim.vvp")
    cmd = [IVERILOG_BIN, "-g2012", "-o", out_vvp] + sv_files
    code, out, err = run_cmd(cmd)
    if code != 0:
        print(f"[ERROR] Failed to compile SystemVerilog testbench: {err}")
        return False
    return True

def convert_elf_to_hex(elf_path, hex_path):
    bin_path = elf_path + ".bin"
    code, out, err = run_cmd([OBJCOPY_BIN, "-O", "binary", elf_path, bin_path])
    if code != 0 or not os.path.exists(bin_path):
        return False

    with open(bin_path, "rb") as f:
        data = f.read()

    with open(hex_path, "w") as f:
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4:
                chunk = chunk.ljust(4, b'\x00')
            val = int.from_bytes(chunk, byteorder='little')
            f.write(f"{val:08X}\n")

    return True

def run_tests():
    test_dirs = [
        os.path.join(ARCH_TEST_DIR, "tests", "rv32i", "I"),
        os.path.join(ARCH_TEST_DIR, "tests", "rv32i", "Zicsr"),
    ]
    test_files = []
    for td in test_dirs:
        test_files.extend(glob.glob(os.path.join(td, "*.S")))

    if not test_files:
        print("[WARNING] No test .S files found in riscv-arch-test directory.")
        return True

    print(f"[INFO] Discovered {len(test_files)} riscv-arch-test assembly test files (I + Zicsr).")
    
    env_inc_dir = os.path.join(ARCH_TEST_DIR, "tests", "env")
    rv32i_inc_dir = os.path.join(ARCH_TEST_DIR, "tests", "rv32i")

    passed = 0
    failed = 0
    vvp_sim = os.path.join(BUILD_DIR, "sim.vvp")

    for s_file in sorted(test_files):
        tname = os.path.basename(s_file).replace(".S", "")
        elf_file = os.path.join(BUILD_DIR, f"{tname}.elf")
        hex_file = os.path.join(BUILD_DIR, f"{tname}.hex")

        linker_script = os.path.join(REPO_DIR, "scripts", "link.ld")
        cmd = [
            GCC_BIN, "-march=rv32i_zicsr", "-mabi=ilp32", "-nostdlib", "-static",
            "-Wl,--build-id=none", "-Wl,-N",
            "-I", TARGET_ENV_DIR,
            "-I", env_inc_dir,
            "-I", rv32i_inc_dir,
            "-DTEST_CASE_1=1",
            "-DTEST_XLEN=32",
            "-DTEST_FLEN=0",
            "-DRVTEST_SELFCHECK=1",
            f"-DTEST_FILE=\"{tname}.S\"",
            "-DSIGNATURE_FILE=\"empty_sig.h\"",
            "-T", linker_script,
            s_file, "-o", elf_file
        ]
        code, out, err = run_cmd(cmd)
        if code != 0:
            print(f"[FAIL] {tname}: GCC compilation failed: {err}")
            failed += 1
            continue

        if not convert_elf_to_hex(elf_file, hex_file):
            print(f"[FAIL] {tname}: Hex conversion failed")
            failed += 1
            continue

        # Run Icarus Verilog simulation
        sim_cmd = [VVP_BIN, vvp_sim, f"+HEX_FILE={hex_file}"]
        code, out, err = run_cmd(sim_cmd, cwd=BUILD_DIR)
        
        if code == 0 and "[PASS]" in out:
            print(f"[PASS] {tname}")
            passed += 1
        else:
            print(f"[FAIL] {tname}: {out} {err}")
            failed += 1

    print("\n" + "="*60)
    print(f"RISC-V Architectural Compliance Test Results: {passed} PASSED, {failed} FAILED out of {len(test_files)} tests.")
    print("="*60)

    return failed == 0

def main():
    setup_repo()
    if not compile_verilog():
        sys.exit(1)
    if not run_tests():
        sys.exit(1)

if __name__ == "__main__":
    main()
