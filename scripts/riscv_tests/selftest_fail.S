// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// Negative self-test for scripts/run_riscv_tests.py: test case 2 deliberately
// expects 1 + 1 == 3. The harness must report this as FAIL with TESTNUM=2;
// if it ever reports PASS (or a different TESTNUM), the halt/tohost plumbing
// between riscv-tests and sim/tb_hex_runner.veryl is broken and every other
// result from the run is meaningless.

#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN

  TEST_RR_OP( 2, add, 3, 1, 1 );

  TEST_PASSFAIL

RVTEST_CODE_END

  .data
RVTEST_DATA_BEGIN

  TEST_DATA

RVTEST_DATA_END
