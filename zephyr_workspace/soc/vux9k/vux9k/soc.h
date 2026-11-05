/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __RISCV_VUX9K_SOC_H_
#define __RISCV_VUX9K_SOC_H_

#include <zephyr/sys/util.h>
#include <zephyr/arch/riscv/sys_io.h>

#ifndef _ASMLANGUAGE
/* The accessors LiteX drivers (uart_liteuart.c) use: one register per 32-bit word */
static inline unsigned char litex_read8(unsigned long addr)
{
	return sys_read8(addr);
}

static inline void litex_write8(unsigned char value, unsigned long addr)
{
	sys_write8(value, addr);
}
#endif

#endif /* __RISCV_VUX9K_SOC_H_ */
