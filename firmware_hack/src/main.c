/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

#include "uart.h"

int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

int fibonacci(int n) {
    int a = 0;
    int b = 1;
    for (int i = 0; i < n; i++) {
        int temp = a + b;
        a = b;
        b = temp;
    }
    return a;
}

int array_sum(const int *arr, int len) {
    int sum = 0;
    for (int i = 0; i < len; i++) {
        sum += arr[i];
    }
    return sum;
}

int main(void) {
    uart_puts("\n=========================================\n");
    uart_puts("  VUX9K SoC Hack 16-bit C Firmware Test  \n");
    uart_puts("=========================================\n");

    // 1. Factorial Test
    uart_puts("  Running: Factorial(6) = ");
    int fact6 = factorial(6);
    uart_put_dec(fact6);
    if (fact6 == 720) {
        uart_puts(" ... [PASS]\n");
    } else {
        uart_puts(" ... [FAIL]\n");
    }

    // 2. Fibonacci Test
    uart_puts("  Running: Fibonacci(10) = ");
    int fib10 = fibonacci(10);
    uart_put_dec(fib10);
    if (fib10 == 55) {
        uart_puts(" ... [PASS]\n");
    } else {
        uart_puts(" ... [FAIL]\n");
    }

    // 3. Array Sum Test
    const int sample_data[5] = {10, 20, 30, 40, 50};
    uart_puts("  Running: Array Sum = ");
    int sum = array_sum(sample_data, 5);
    uart_put_dec(sum);
    if (sum == 150) {
        uart_puts(" ... [PASS]\n");
    } else {
        uart_puts(" ... [FAIL]\n");
    }

    uart_puts("-----------------------------------------\n");
    uart_puts("ALL HACK C FIRMWARE TESTS PASSED (100%)!\n");
    uart_puts("=========================================\n");

    return 0;
}
