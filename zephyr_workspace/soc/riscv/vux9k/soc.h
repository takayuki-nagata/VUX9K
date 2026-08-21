/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef SOC_RISCV_VUX9K_SOC_H_
#define SOC_RISCV_VUX9K_SOC_H_

#include <zephyr/sys/util.h>

#define VUX9K_I_ROM_BASE  0x00000000
#define VUX9K_I_ROM_SIZE  0x00040000 /* 256 KB */

#define VUX9K_D_RAM_BASE  0x20000000
#define VUX9K_D_RAM_SIZE  0x00020000 /* 128 KB */

#define VUX9K_UART_BASE   0x40000000
#define VUX9K_TIMER_BASE  0x40001000
#define VUX9K_SDCARD_BASE 0x40002000

#endif /* SOC_RISCV_VUX9K_SOC_H_ */
