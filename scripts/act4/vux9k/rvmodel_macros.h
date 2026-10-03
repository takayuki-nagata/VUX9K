// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT
// RVMODEL macros for VUX9K's riscv-tests harness (sim/tb_hex_runner.veryl), after the
// upstream example config/cores/cve2/cv32e20/rvmodel_macros.h.

#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H

#define RVMODEL_DATA_SECTION

#define STANDARD_SM_SUPPORTED

##### TERMINATION #####

// tohost (tb_hex_runner's TOHOST_ADDR, scripts/riscv_tests/link.ld): 1 = PASS, odd = FAIL
#define RVMODEL_HALT_PASS  \
  li x1, 1                ;\
  li t0, 0xF0000000       ;\
  write_halt_pass:        ;\
    sw x1, 0(t0)          ;\
  self_loop_pass:         ;\
    j self_loop_pass      ;\

#define RVMODEL_HALT_FAIL \
  li x1, 3                ;\
  li t0, 0xF0000000       ;\
  write_halt_fail:        ;\
    sw x1, 0(t0)          ;\
  self_loop_fail:         ;\
    j self_loop_fail      ;\

##### IO #####

// The failure details: bytes to tb_hex_runner's console word, next to tohost
#define RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR_PTR) \
1:                           ;                        \
  lbu  _R1, 0(_STR_PTR)      ;                        \
  beqz _R1, 3f               ;                        \
  li   _R2, 0xF0000004       ;                        \
  sw   _R1, 0(_R2)           ;                        \
  addi _STR_PTR, _STR_PTR, 1 ;                        \
  j 1b                       ;                        \
3:

##### Interrupts (none in the harness; UDB *_INTR_IMPL are false) #####

#define RVMODEL_INTERRUPT_LATENCY 10
#define RVMODEL_TIMER_INT_SOON_DELAY 100

// No interrupt sources in the harness: a test that raises one through these fails, and
// is listed in run_riscv_tests.py's EXPECTED_FAILURES_ACT4
#define RVMODEL_SET_MEXT_INT(_R1, _R2)
#define RVMODEL_CLR_MEXT_INT(_R1, _R2)
#define RVMODEL_SET_MSW_INT(_R1, _R2)
#define RVMODEL_CLR_MSW_INT(_R1, _R2)
#define RVMODEL_SET_SEXT_INT(_R1, _R2)
#define RVMODEL_CLR_SEXT_INT(_R1, _R2)
#define RVMODEL_SET_SSW_INT(_R1, _R2)
#define RVMODEL_CLR_SSW_INT(_R1, _R2)

#endif // _RVMODEL_MACROS_H
