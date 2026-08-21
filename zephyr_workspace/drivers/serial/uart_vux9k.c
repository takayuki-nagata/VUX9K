/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

#define DT_DRV_COMPAT vux9k_uart

#include <zephyr/kernel.h>
#include <zephyr/arch/cpu.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/sys_io.h>

#define REG_DATA(base)   ((base) + 0x00)
#define REG_STATUS(base) ((base) + 0x04)

#define STATUS_RX_EMPTY  (1U << 0)
#define STATUS_TX_FULL   (1U << 1)

struct uart_vux9k_config {
	mm_reg_t base;
};

struct uart_vux9k_data {
	/* driver runtime state if needed */
};

static int uart_vux9k_poll_in(const struct device *dev, unsigned char *p_char)
{
	const struct uart_vux9k_config *config = dev->config;
	uint32_t status = sys_read32(REG_STATUS(config->base));

	if (status & STATUS_RX_EMPTY) {
		return -1;
	}

	*p_char = (unsigned char)(sys_read32(REG_DATA(config->base)) & 0xFF);
	return 0;
}

static void uart_vux9k_poll_out(const struct device *dev, unsigned char out_char)
{
	const struct uart_vux9k_config *config = dev->config;

	while (sys_read32(REG_STATUS(config->base)) & STATUS_TX_FULL) {
		/* Spin wait until TX FIFO is not full */
	}

	sys_write32((uint32_t)out_char, REG_DATA(config->base));
}

static int uart_vux9k_err_check(const struct device *dev)
{
	ARG_UNUSED(dev);
	return 0;
}

static const struct uart_driver_api uart_vux9k_driver_api = {
	.poll_in = uart_vux9k_poll_in,
	.poll_out = uart_vux9k_poll_out,
	.err_check = uart_vux9k_err_check,
};

static int uart_vux9k_init(const struct device *dev)
{
	ARG_UNUSED(dev);
	return 0;
}

#define UART_VUX9K_INIT(n)                                                     \
	static const struct uart_vux9k_config uart_vux9k_config_##n = {        \
		.base = DT_INST_REG_ADDR(n),                                   \
	};                                                                     \
	static struct uart_vux9k_data uart_vux9k_data_##n;                     \
	DEVICE_DT_INST_DEFINE(n, uart_vux9k_init, NULL,                        \
			      &uart_vux9k_data_##n,                            \
			      &uart_vux9k_config_##n, PRE_KERNEL_1,            \
			      CONFIG_SERIAL_INIT_PRIORITY,                     \
			      &uart_vux9k_driver_api);

DT_INST_FOREACH_STATUS_OKAY(UART_VUX9K_INIT)
