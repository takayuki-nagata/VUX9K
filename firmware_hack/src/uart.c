/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

#include "uart.h"

void uart_putc(char c) {
    *HACK_UART_TX = (int)c;
}

void uart_puts(const char *s) {
    while (*s) {
        uart_putc(*s++);
    }
}

void uart_put_dec(int val) {
    if (val == 0) {
        uart_putc('0');
        return;
    }
    if (val < 0) {
        uart_putc('-');
        val = -val;
    }

    char buf[8];
    int i = 0;
    while (val > 0) {
        buf[i++] = (char)('0' + (val % 10));
        val /= 10;
    }
    while (i > 0) {
        uart_putc(buf[--i]);
    }
}

void uart_put_hex(unsigned int val) {
    const char hex_chars[] = "0123456789ABCDEF";
    for (int i = 12; i >= 0; i -= 4) {
        uart_putc(hex_chars[(val >> i) & 0xF]);
    }
}
