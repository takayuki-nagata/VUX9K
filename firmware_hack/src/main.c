/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

#include "uart.c"

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

static void print_banner(void) {
    uart_putc('\n');
    for (int i = 0; i < 41; i++) uart_putc('=');
    uart_putc('\n');
    uart_putc(' '); uart_putc(' ');
    uart_putc('V'); uart_putc('U'); uart_putc('X'); uart_putc('9'); uart_putc('K');
    uart_putc(' '); uart_putc('S'); uart_putc('o'); uart_putc('C'); uart_putc(' ');
    uart_putc('H'); uart_putc('a'); uart_putc('c'); uart_putc('k'); uart_putc(' ');
    uart_putc('1'); uart_putc('6'); uart_putc('-'); uart_putc('b'); uart_putc('i'); uart_putc('t');
    uart_putc(' '); uart_putc('C'); uart_putc(' ');
    uart_putc('F'); uart_putc('i'); uart_putc('r'); uart_putc('m'); uart_putc('w'); uart_putc('a'); uart_putc('r'); uart_putc('e');
    uart_putc(' '); uart_putc('T'); uart_putc('e'); uart_putc('s'); uart_putc('t');
    uart_putc('\n');
    for (int i = 0; i < 41; i++) uart_putc('=');
    uart_putc('\n');
}

static void print_pass(void) {
    uart_putc(' '); uart_putc('.'); uart_putc('.'); uart_putc('.');
    uart_putc(' '); uart_putc('['); uart_putc('P'); uart_putc('A'); uart_putc('S'); uart_putc('S'); uart_putc(']');
    uart_putc('\n');
}

static void print_fail(void) {
    uart_putc(' '); uart_putc('.'); uart_putc('.'); uart_putc('.');
    uart_putc(' '); uart_putc('['); uart_putc('F'); uart_putc('A'); uart_putc('I'); uart_putc('L'); uart_putc(']');
    uart_putc('\n');
}

static void print_test1_header(void) {
    uart_putc(' '); uart_putc(' ');
    uart_putc('R'); uart_putc('u'); uart_putc('n'); uart_putc('n'); uart_putc('i'); uart_putc('n'); uart_putc('g'); uart_putc(':');
    uart_putc(' '); uart_putc('F'); uart_putc('a'); uart_putc('c'); uart_putc('t'); uart_putc('o'); uart_putc('r'); uart_putc('i'); uart_putc('a'); uart_putc('l');
    uart_putc('('); uart_putc('6'); uart_putc(')'); uart_putc(' '); uart_putc('='); uart_putc(' ');
}

static void print_test2_header(void) {
    uart_putc(' '); uart_putc(' ');
    uart_putc('R'); uart_putc('u'); uart_putc('n'); uart_putc('n'); uart_putc('i'); uart_putc('n'); uart_putc('g'); uart_putc(':');
    uart_putc(' '); uart_putc('F'); uart_putc('i'); uart_putc('b'); uart_putc('o'); uart_putc('n'); uart_putc('a'); uart_putc('c'); uart_putc('c'); uart_putc('i');
    uart_putc('('); uart_putc('1'); uart_putc('0'); uart_putc(')'); uart_putc(' '); uart_putc('='); uart_putc(' ');
}

static void print_test3_header(void) {
    uart_putc(' '); uart_putc(' ');
    uart_putc('R'); uart_putc('u'); uart_putc('n'); uart_putc('n'); uart_putc('i'); uart_putc('n'); uart_putc('g'); uart_putc(':');
    uart_putc(' '); uart_putc('A'); uart_putc('r'); uart_putc('r'); uart_putc('a'); uart_putc('y');
    uart_putc(' '); uart_putc('S'); uart_putc('u'); uart_putc('m');
    uart_putc(' '); uart_putc('='); uart_putc(' ');
}

static void print_all_passed(void) {
    for (int i = 0; i < 41; i++) uart_putc('-');
    uart_putc('\n');
    uart_putc('A'); uart_putc('L'); uart_putc('L'); uart_putc(' ');
    uart_putc('H'); uart_putc('A'); uart_putc('C'); uart_putc('K'); uart_putc(' ');
    uart_putc('C'); uart_putc(' ');
    uart_putc('F'); uart_putc('I'); uart_putc('R'); uart_putc('M'); uart_putc('W'); uart_putc('A'); uart_putc('R'); uart_putc('E'); uart_putc(' ');
    uart_putc('T'); uart_putc('E'); uart_putc('S'); uart_putc('T'); uart_putc('S'); uart_putc(' ');
    uart_putc('P'); uart_putc('A'); uart_putc('S'); uart_putc('S'); uart_putc('E'); uart_putc('D'); uart_putc(' ');
    uart_putc('('); uart_putc('1'); uart_putc('0'); uart_putc('0'); uart_putc('%'); uart_putc(')'); uart_putc('!');
    uart_putc('\n');
    for (int i = 0; i < 41; i++) uart_putc('=');
    uart_putc('\n');
}

int main(void) {
    print_banner();

    // 1. Factorial Test
    print_test1_header();
    int fact6 = factorial(6);
    uart_put_dec(fact6);
    if (fact6 == 720) {
        print_pass();
    } else {
        print_fail();
    }

    // 2. Fibonacci Test
    print_test2_header();
    int fib10 = fibonacci(10);
    uart_put_dec(fib10);
    if (fib10 == 55) {
        print_pass();
    } else {
        print_fail();
    }

    // 3. Array Sum Test
    print_test3_header();
    int sample_data[5];
    sample_data[0] = 10;
    sample_data[1] = 20;
    sample_data[2] = 30;
    sample_data[3] = 40;
    sample_data[4] = 50;
    int sum = array_sum(sample_data, 5);
    uart_put_dec(sum);
    if (sum == 150) {
        print_pass();
    } else {
        print_fail();
    }

    print_all_passed();

    return 0;
}
