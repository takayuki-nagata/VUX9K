/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

/* Rust staticlib entry function */
extern void rust_main(void);

int main(void)
{
	printk("\n========================================\n");
	printk("  Zephyr RTOS Booting on VUX9K SoC!     \n");
	printk("  Dual-ISA (RV32I & Hack) FPGA Platform \n");
	printk("========================================\n");

	printk("[Zephyr Kernel] Kernel initialized successfully.\n");
	printk("[Zephyr Kernel] Handing over execution to Rust application...\n");

	/* Call Rust application main entry */
	rust_main();

	printk("\n[Zephyr Kernel] Rust application returned. Entering sleep loop.\n");

	while (1) {
		k_sleep(K_FOREVER);
	}

	return 0;
}
