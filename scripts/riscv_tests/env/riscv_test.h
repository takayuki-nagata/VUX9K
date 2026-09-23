// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// riscv-tests env/p, with RVTEST_PASS/RVTEST_FAIL reporting straight to tohost.
//
// Upstream env/p reports results via `ecall` -> trap_vector -> write_tohost, so
// every result depends on the CPU's trap path. unified_cpu currently never takes
// ECALL/EBREAK/MRET/interrupt traps (the decode was lost in commit 02a105a, see
// AGENTS.md), which would make every test hang instead of reporting. Jumping to
// write_tohost directly keeps rv32ui results independent of trap support; the
// trap path itself is covered by rv32mi, which stays in the suite as expected
// failures until the CPU is fixed.

#ifndef VUX9K_RISCV_TEST_H
#define VUX9K_RISCV_TEST_H

#include_next "riscv_test.h"

#undef RVTEST_PASS
#define RVTEST_PASS                                                     \
        fence;                                                          \
        li TESTNUM, 1;                                                  \
        j write_tohost

#undef RVTEST_FAIL
#define RVTEST_FAIL                                                     \
        fence;                                                          \
1:      beqz TESTNUM, 1b;                                               \
        sll TESTNUM, TESTNUM, 1;                                        \
        or TESTNUM, TESTNUM, 1;                                         \
        j write_tohost

#endif
