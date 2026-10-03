// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT
//
// The ACT4 harness self-test (run_riscv_tests.py --suite act4): prints through
// RVMODEL_IO_WRITE_STR and stops with RVMODEL_HALT_FAIL, linked like the ACT4 tests
// (vux9k/link.ld). A run must report FAIL (TESTNUM=1) and "act4 selftest" on the console,
// or the macros, the linker script or the testbench's tohost/console are broken.
#include "rvmodel_macros.h"

    .section .text.init
    .globl rvtest_entry_point
rvtest_entry_point:
    la a0, msg
    RVMODEL_IO_WRITE_STR(t1, t2, t3, a0)
    RVMODEL_HALT_FAIL

    .section .rodata
msg:
    .string "act4 selftest\n"
