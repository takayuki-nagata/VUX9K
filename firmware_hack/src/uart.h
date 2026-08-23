/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

#ifndef HACK_UART_H
#define HACK_UART_H

#define HACK_UART_TX ((volatile int *)24576)

void uart_putc(char c);
void uart_puts(const char *s);
void uart_put_dec(int val);
void uart_put_hex(unsigned int val);

#endif /* HACK_UART_H */
