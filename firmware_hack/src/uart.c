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
    if (val < 0) {
        uart_putc('-');
        val = -val;
    }
    if (val >= 10) {
        int q = 0;
        int r = val;
        while (r >= 10) {
            r -= 10;
            q++;
        }
        uart_put_dec(q);
        uart_putc((char)('0' + r));
    } else {
        uart_putc((char)('0' + val));
    }
}
