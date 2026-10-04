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
#define STATUS_OVERRUN   (1U << 2) /* sticky; reading the status register clears it */
#define STATUS_FRAME_ERR (1U << 3) /* sticky; reading the status register clears it */

struct uart_vux9k_config {
	mm_reg_t base;
#ifdef CONFIG_UART_INTERRUPT_DRIVEN
	void (*irq_config_func)(const struct device *dev);
#endif
};

struct uart_vux9k_data {
#ifdef CONFIG_UART_INTERRUPT_DRIVEN
	const struct device *dev;
	uart_irq_callback_user_data_t cb;
	void *cb_data;
	bool rx_enabled;
	bool tx_enabled;
	bool in_callback;
	struct k_timer tx_timer;
#endif
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
	const struct uart_vux9k_config *config = dev->config;
	uint32_t status = sys_read32(REG_STATUS(config->base));
	int err = 0;

	if (status & STATUS_OVERRUN) {
		err |= UART_ERROR_OVERRUN;
	}
	if (status & STATUS_FRAME_ERR) {
		err |= UART_ERROR_FRAMING;
	}
	return err;
}

#ifdef CONFIG_UART_INTERRUPT_DRIVEN
/*
 * The SoC raises the machine external interrupt (mcause 11) while the RX FIFO holds
 * data; it is level and has no enable bit of its own, so mie.MEIE (irq_enable/
 * irq_disable) is the only way to stop it. There is no TX interrupt: the TX side calls
 * the callback itself, at once from uart_irq_tx_enable() and then from tx_timer for as
 * long as TX stays enabled (the FIFO holds 32 bytes, about 2.8 ms of output at 115200).
 */
#define TX_RETRY_PERIOD K_USEC(700) /* a quarter of the TX FIFO */

static int uart_vux9k_fifo_fill(const struct device *dev, const uint8_t *tx_data, int len)
{
	const struct uart_vux9k_config *config = dev->config;
	int i;

	for (i = 0; i < len && !(sys_read32(REG_STATUS(config->base)) & STATUS_TX_FULL); i++) {
		sys_write32(tx_data[i], REG_DATA(config->base));
	}
	return i;
}

static int uart_vux9k_fifo_read(const struct device *dev, uint8_t *rx_data, const int size)
{
	const struct uart_vux9k_config *config = dev->config;
	int i;

	for (i = 0; i < size && !(sys_read32(REG_STATUS(config->base)) & STATUS_RX_EMPTY);
	     i++) {
		rx_data[i] = (uint8_t)(sys_read32(REG_DATA(config->base)) & 0xFF);
	}
	return i;
}

/* Runs the callback once; interrupts are off (an ISR, or irq_lock in a thread) */
static void uart_vux9k_run_callback(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	if (data->cb && !data->in_callback) {
		data->in_callback = true;
		data->cb(dev, data->cb_data);
		data->in_callback = false;
	}
	if (data->tx_enabled) {
		k_timer_start(&data->tx_timer, TX_RETRY_PERIOD, K_NO_WAIT);
	}
}

static void uart_vux9k_tx_timer_expiry(struct k_timer *timer)
{
	struct uart_vux9k_data *data = CONTAINER_OF(timer, struct uart_vux9k_data, tx_timer);

	if (data->tx_enabled) {
		uart_vux9k_run_callback(data->dev);
	}
}

static void uart_vux9k_irq_tx_enable(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;
	unsigned int key = irq_lock();

	data->tx_enabled = true;
	/* From inside the callback, the retry timer started after it returns takes over */
	if (!data->in_callback) {
		uart_vux9k_run_callback(dev);
	}
	irq_unlock(key);
}

static void uart_vux9k_irq_tx_disable(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	data->tx_enabled = false;
	k_timer_stop(&data->tx_timer);
}

static int uart_vux9k_irq_tx_ready(const struct device *dev)
{
	const struct uart_vux9k_config *config = dev->config;
	struct uart_vux9k_data *data = dev->data;

	return data->tx_enabled && !(sys_read32(REG_STATUS(config->base)) & STATUS_TX_FULL);
}

static void uart_vux9k_irq_rx_enable(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	data->rx_enabled = true;
	irq_enable(DT_INST_IRQN(0));
}

static void uart_vux9k_irq_rx_disable(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	data->rx_enabled = false;
	irq_disable(DT_INST_IRQN(0));
}

static int uart_vux9k_irq_rx_ready(const struct device *dev)
{
	const struct uart_vux9k_config *config = dev->config;

	return !(sys_read32(REG_STATUS(config->base)) & STATUS_RX_EMPTY);
}

static int uart_vux9k_irq_is_pending(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	return (data->rx_enabled && uart_vux9k_irq_rx_ready(dev)) ||
	       uart_vux9k_irq_tx_ready(dev);
}

static int uart_vux9k_irq_update(const struct device *dev)
{
	ARG_UNUSED(dev);
	return 1;
}

static void uart_vux9k_irq_callback_set(const struct device *dev,
					uart_irq_callback_user_data_t cb, void *cb_data)
{
	struct uart_vux9k_data *data = dev->data;

	data->cb = cb;
	data->cb_data = cb_data;
}

static void uart_vux9k_isr(const struct device *dev)
{
	struct uart_vux9k_data *data = dev->data;

	/* Level-triggered: with nobody to read the FIFO, it would re-enter forever */
	if (!data->cb) {
		uart_vux9k_irq_rx_disable(dev);
		return;
	}
	uart_vux9k_run_callback(dev);
}
#endif /* CONFIG_UART_INTERRUPT_DRIVEN */

static const struct uart_driver_api uart_vux9k_driver_api = {
	.poll_in = uart_vux9k_poll_in,
	.poll_out = uart_vux9k_poll_out,
	.err_check = uart_vux9k_err_check,
#ifdef CONFIG_UART_INTERRUPT_DRIVEN
	/* No irq_tx_complete: the status register can't tell when the last bit left */
	.fifo_fill = uart_vux9k_fifo_fill,
	.fifo_read = uart_vux9k_fifo_read,
	.irq_tx_enable = uart_vux9k_irq_tx_enable,
	.irq_tx_disable = uart_vux9k_irq_tx_disable,
	.irq_tx_ready = uart_vux9k_irq_tx_ready,
	.irq_rx_enable = uart_vux9k_irq_rx_enable,
	.irq_rx_disable = uart_vux9k_irq_rx_disable,
	.irq_rx_ready = uart_vux9k_irq_rx_ready,
	.irq_is_pending = uart_vux9k_irq_is_pending,
	.irq_update = uart_vux9k_irq_update,
	.irq_callback_set = uart_vux9k_irq_callback_set,
#endif
};

static int uart_vux9k_init(const struct device *dev)
{
#ifdef CONFIG_UART_INTERRUPT_DRIVEN
	const struct uart_vux9k_config *config = dev->config;
	struct uart_vux9k_data *data = dev->data;

	data->dev = dev;
	k_timer_init(&data->tx_timer, uart_vux9k_tx_timer_expiry, NULL);
	config->irq_config_func(dev);
#else
	ARG_UNUSED(dev);
#endif
	return 0;
}

#ifdef CONFIG_UART_INTERRUPT_DRIVEN
/* The SoC has one UART and one external interrupt line; MEIE stays off until rx_enable */
BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(DT_DRV_COMPAT) <= 1, "one VUX9K UART only");
#define UART_VUX9K_IRQ_CONFIG(n)                                               \
	static void uart_vux9k_irq_config_##n(const struct device *dev)        \
	{                                                                      \
		ARG_UNUSED(dev);                                               \
		IRQ_CONNECT(DT_INST_IRQN(n), 0, uart_vux9k_isr,                \
			    DEVICE_DT_INST_GET(n), 0);                         \
	}
#define UART_VUX9K_IRQ_CONFIG_FUNC(n) .irq_config_func = uart_vux9k_irq_config_##n,
#else
#define UART_VUX9K_IRQ_CONFIG(n)
#define UART_VUX9K_IRQ_CONFIG_FUNC(n)
#endif

#define UART_VUX9K_INIT(n)                                                     \
	UART_VUX9K_IRQ_CONFIG(n)                                               \
	static const struct uart_vux9k_config uart_vux9k_config_##n = {        \
		.base = DT_INST_REG_ADDR(n),                                   \
		UART_VUX9K_IRQ_CONFIG_FUNC(n)                                  \
	};                                                                     \
	static struct uart_vux9k_data uart_vux9k_data_##n;                     \
	DEVICE_DT_INST_DEFINE(n, uart_vux9k_init, NULL,                        \
			      &uart_vux9k_data_##n,                            \
			      &uart_vux9k_config_##n, PRE_KERNEL_1,            \
			      CONFIG_SERIAL_INIT_PRIORITY,                     \
			      &uart_vux9k_driver_api);

DT_INST_FOREACH_STATUS_OKAY(UART_VUX9K_INIT)
