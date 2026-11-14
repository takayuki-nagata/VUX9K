// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// sysmon's system calls (src/lib.rs is the program: uClibc has no Rust target, so the Rust code
// is a no_std staticlib, built PIC as buildroot builds this C, linked into this executable):
//   - files read whole (/proc/uptime, /proc/meminfo, /proc/loadavg);
//   - the LEDs through the LED class, /sys/class/leds/ledN/brightness (the device tree's
//     gpio-leds), each opened once;
//   - the button S2 through the GPIO character device: the gpiochip labelled "button" (the
//     device tree's gpio-mmio on the button's register), its line 0 requested as an input,
//     its value read with each poll (no interrupt);
//   - nanosleep, which a signal ends early, and SIGTERM/SIGINT as a flag;
//   - the page cache dropped through /proc/sys/vm/drop_caches (the reclaim's decision is
//     lib.rs').
// No stdio: see memprobe.

#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <signal.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define LEDS 6

int sysmon_main(int argc, char **argv);

static volatile sig_atomic_t stop_signal;
static int led_fd[LEDS] = {-1, -1, -1, -1, -1, -1};
// The button's line: -1 not opened yet, -2 none
static int button_fd = -1;

long vux9k_read_file(const char *path, unsigned char *buf, unsigned long cap) {
    int fd = open(path, O_RDONLY);
    long len = 0, n = 0;
    if (fd < 0)
        return -1;
    while (len < (long)cap && (n = read(fd, buf + len, cap - len)) > 0)
        len += n;
    close(fd);
    return n < 0 ? -1 : len;
}

int vux9k_led(unsigned n, int on) {
    static char path[] = "/sys/class/leds/led?/brightness";
    if (n >= LEDS)
        return -1;
    if (led_fd[n] < 0) {
        path[sizeof "/sys/class/leds/led" - 1] = (char)('0' + n);
        if ((led_fd[n] = open(path, O_WRONLY)) < 0)
            return -1;
    }
    return write(led_fd[n], on ? "1" : "0", 1) == 1 ? 0 : -1;
}

static int open_button(void) {
    static char path[] = "/dev/gpiochip?";
    for (int i = 0; i < 4; i++) {
        struct gpiochip_info info;
        path[sizeof "/dev/gpiochip" - 1] = (char)('0' + i);
        int chip = open(path, O_RDONLY);
        if (chip < 0)
            continue;
        memset(&info, 0, sizeof info);
        if (ioctl(chip, GPIO_GET_CHIPINFO_IOCTL, &info) == 0 && !strcmp(info.label, "button")) {
            struct gpio_v2_line_request req;
            memset(&req, 0, sizeof req);
            req.offsets[0] = 0;
            req.num_lines = 1;
            req.config.flags = GPIO_V2_LINE_FLAG_INPUT;
            strcpy(req.consumer, "sysmon");
            int ok = ioctl(chip, GPIO_V2_GET_LINE_IOCTL, &req) == 0;
            close(chip);
            return ok ? req.fd : -2;
        }
        close(chip);
    }
    return -2;
}

// 1 pressed, 0 not, -1 no button (none in the device tree, or another process holds its line)
int vux9k_button(void) {
    struct gpio_v2_line_values v;
    if (button_fd == -1)
        button_fd = open_button();
    if (button_fd < 0)
        return -1;
    memset(&v, 0, sizeof v);
    v.mask = 1;
    if (ioctl(button_fd, GPIO_V2_LINE_GET_VALUES_IOCTL, &v) < 0)
        return -1;
    return (int)(v.bits & 1);
}

void vux9k_sleep_ms(unsigned ms) {
    struct timespec t = {ms / 1000, (long)(ms % 1000) * 1000000L};
    nanosleep(&t, 0);
}

int vux9k_stopping(void) {
    return stop_signal != 0;
}

// One line in one write (the console puts other processes' output between separate ones)
void vux9k_say(const unsigned char *p, unsigned long len) {
    char line[128];
    if (len > sizeof line - 1)
        len = sizeof line - 1;
    memcpy(line, p, len);
    line[len] = '\n';
    write(1, line, len + 1);
}

// what: '1' drops the clean page cache, '4' drops nothing but stops the kernel's
// "drop_caches: N" line on the console after this write (the file takes 1 to 4 only)
int vux9k_drop_caches(unsigned char what) {
    int fd = open("/proc/sys/vm/drop_caches", O_WRONLY);
    int ok;
    if (fd < 0)
        return -1;
    ok = write(fd, &what, 1) == 1;
    close(fd);
    return ok ? 0 : -1;
}

static void on_stop(int sig) {
    stop_signal = sig;
}

int main(int argc, char **argv) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    sigaction(SIGTERM, &sa, 0);
    sigaction(SIGINT, &sa, 0);
    return sysmon_main(argc, argv);
}
