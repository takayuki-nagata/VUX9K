/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 *
 * Interrupt-driven UART echo: the UART callback moves received bytes into rx and wakes
 * main, which sleeps on a semaphore (no polling: without the RX interrupt nothing comes
 * back). At each '\n', main sends the line back in upper case (up to 256 bytes) through
 * tx, which the callback drains into the TX FIFO, then "rx=<bytes in the line>
 * drop=<bytes rx had no room for> err=<uart_err_check()>". Everything goes out through
 * the interrupt-driven API; there is no printk (cbprintf alone would not fit in the
 * board's 14 KB with the rest).
 */

#include <zephyr/kernel.h>
#include <zephyr/drivers/uart.h>
#include <string.h>

static const struct device *const uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

/*
 * One producer and one consumer each; the indices only grow (wrap at 2^32). The
 * barriers keep the compiler from moving a buf access past the index update that
 * hands the slot over.
 */
#define RING_SIZE 256
struct ring {
	uint8_t buf[RING_SIZE];
	volatile uint32_t head; /* written by the producer */
	volatile uint32_t tail; /* written by the consumer */
};

static struct ring rx; /* the callback produces, main consumes */
static struct ring tx; /* main produces, the callback consumes */
static K_SEM_DEFINE(rx_sem, 0, 1);
static volatile uint32_t rx_dropped;

static void uart_cb(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);
	uart_irq_update(dev);

	while (uart_irq_rx_ready(dev)) {
		uint8_t c;

		if (uart_fifo_read(dev, &c, 1) != 1) {
			break;
		}
		if (rx.head - rx.tail < RING_SIZE) {
			rx.buf[rx.head % RING_SIZE] = c;
			compiler_barrier();
			rx.head++;
		} else {
			rx_dropped++;
		}
		k_sem_give(&rx_sem);
	}

	if (uart_irq_tx_ready(dev)) {
		if (tx.tail == tx.head) {
			uart_irq_tx_disable(dev);
		}
		while (tx.tail != tx.head &&
		       uart_fifo_fill(dev, &tx.buf[tx.tail % RING_SIZE], 1) == 1) {
			compiler_barrier();
			tx.tail++;
		}
	}
}

/* Queues bytes for the callback to send, waiting while tx is full */
static void send(const char *data, uint32_t len)
{
	while (len > 0) {
		while (len > 0 && tx.head - tx.tail < RING_SIZE) {
			tx.buf[tx.head % RING_SIZE] = *data++;
			compiler_barrier();
			tx.head++;
			len--;
		}
		uart_irq_tx_enable(uart);
		if (len > 0) {
			k_msleep(1);
		}
	}
}

static void send_str(const char *s)
{
	send(s, strlen(s));
}

static void send_u32(uint32_t v)
{
	char digits[10];
	int n = sizeof(digits);

	do {
		digits[--n] = '0' + v % 10;
		v /= 10;
	} while (v > 0);
	send(&digits[n], sizeof(digits) - n);
}

int main(void)
{
	static char line[RING_SIZE];
	uint32_t len = 0;

	uart_irq_callback_set(uart, uart_cb);
	send_str("irq_echo ready\n");
	uart_irq_rx_enable(uart);

	for (;;) {
		k_sem_take(&rx_sem, K_FOREVER);
		while (rx.tail != rx.head) {
			char c = rx.buf[rx.tail % RING_SIZE];

			compiler_barrier();
			rx.tail++;
			if (c != '\n') {
				if (len < sizeof(line)) {
					line[len] = (c >= 'a' && c <= 'z') ? c - ('a' - 'A') : c;
				}
				len++;
				continue;
			}
			/* At once: longer than the 32-byte TX FIFO, so the driver's retry runs */
			send(line, MIN(len, sizeof(line)));
			send_str("\nrx=");
			send_u32(len);
			unsigned int key = irq_lock();
			uint32_t dropped = rx_dropped;

			rx_dropped = 0;
			irq_unlock(key);
			send_str(" drop=");
			send_u32(dropped);
			send_str(" err=");
			send_u32(uart_err_check(uart));
			send_str("\n");
			len = 0;
		}
	}
	return 0;
}
