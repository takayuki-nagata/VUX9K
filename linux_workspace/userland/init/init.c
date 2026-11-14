// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// VUX9K's init (/init in the initramfs, the kernel's first process), in place of busybox's:
// busybox init stayed in RAM as a whole copy of busybox (~370 KB, noMMU FLAT: every process
// has its own copy of its executable), which the kernel with the SD card could not spare.
// This one does without exec what the inittab and buildroot's /init script did:
//   - devtmpfs on /dev (the kernel mounts it only for a root that is not an initramfs), the
//     console on fds 0-2, proc on /proc;
//   - the SD card's FAT32 partition on /mnt/sd, once mmc has found the card (in the
//     background, possibly after init has started: up to CARD_WAIT_MS);
//   - the login shell (-/bin/sh, which reads /etc/profile) on the console, in its own session
//     with the console as its controlling tty, started again when it exits (after a pause if
//     it lived less than a second: an exec that fails would loop);
//   - reaping every orphan the kernel hands to it;
//   - SIGTERM (reboot), SIGUSR1 (halt) and SIGUSR2 (poweroff), which busybox's reboot, halt and
//     poweroff send to process 1: every process sent SIGTERM, then SIGKILL (a shell in /mnt/sd
//     or a program from the card keeps it busy), sync, the card unmounted (or remounted
//     read-only if that fails), then the reboot system call.
// Messages go to the console as "init: ..." lines. No stdio: see memprobe.

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CARD_DEV "/dev/mmcblk0p1"
#define CARD_DIR "/mnt/sd"
#define CARD_WAIT_MS 5000
#define SHELL "/bin/sh"

static volatile sig_atomic_t stop_signal;

static void say(const char *a, const char *b) {
    write(2, "init: ", 6);
    write(2, a, strlen(a));
    if (b) {
        write(2, b, strlen(b));
    }
    write(2, "\n", 1);
}

static void sleep_ms(long ms) {
    struct timespec t = {ms / 1000, (ms % 1000) * 1000000L};
    while (nanosleep(&t, &t) < 0 && errno == EINTR && !stop_signal) {
    }
}

// Milliseconds since boot, wrapping (unsigned): compare differences only
static unsigned long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long)t.tv_sec * 1000UL + (unsigned long)t.tv_nsec / 1000000UL;
}

static void console_on_stdio(void) {
    int fd = open("/dev/console", O_RDWR | O_NOCTTY);
    if (fd < 0) {
        return;
    }
    for (int i = 0; i < 3; i++) {
        if (fd != i) {
            dup2(fd, i);
        }
    }
    if (fd > 2) {
        close(fd);
    }
}

static void mount_card(void) {
    struct stat st;
    unsigned long start = now_ms();
    while (stat(CARD_DEV, &st) < 0 || !S_ISBLK(st.st_mode)) {
        if (now_ms() - start >= CARD_WAIT_MS) {
            say("no card (" CARD_DEV ")", 0);
            return;
        }
        sleep_ms(100);
    }
    if (mount(CARD_DEV, CARD_DIR, "vfat", 0, 0) < 0) {
        say(CARD_DEV " not mounted: ", strerror(errno));
        return;
    }
    say(CARD_DEV " mounted on " CARD_DIR, 0);
}

static pid_t start_shell(void) {
    pid_t pid = vfork();
    if (pid == 0) {
        static char *const argv[] = {"-sh", 0};
        static char *const envp[] = {"HOME=/", "PATH=/sbin:/usr/sbin:/bin:/usr/bin", "SHELL=" SHELL,
                                     "USER=root", "TERM=linux", 0};
        // init ignores these; the shell and its programs must not (SIG_IGN survives execve)
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        setsid();
        int fd = open("/dev/console", O_RDWR);
        if (fd >= 0) {
            ioctl(fd, TIOCSCTTY, 0);
            for (int i = 0; i < 3; i++) {
                dup2(fd, i);
            }
            if (fd > 2) {
                close(fd);
            }
        }
        execve(SHELL, argv, envp);
        // No block of contiguous pages for busybox's copy, typically
        write(2, "init: cannot run " SHELL "\n", sizeof "init: cannot run " SHELL "\n" - 1);
        _exit(127);
    }
    if (pid < 0) {
        say("cannot start " SHELL ": ", strerror(errno));
    }
    return pid;
}

static void on_stop(int sig) {
    stop_signal = sig;
}

static void stop(void) {
    int how = stop_signal == SIGUSR1   ? RB_HALT_SYSTEM
              : stop_signal == SIGUSR2 ? RB_POWER_OFF
                                       : RB_AUTOBOOT;
    say("stopping every process", 0);
    kill(-1, SIGTERM);
    sleep_ms(1000);
    kill(-1, SIGKILL);
    while (waitpid(-1, 0, WNOHANG) > 0) {
    }
    say("syncing, unmounting " CARD_DIR, 0);
    sync();
    if (umount2(CARD_DIR, 0) < 0 && errno != EINVAL) {
        say(CARD_DIR " not unmounted: ", strerror(errno));
        if (mount(0, CARD_DIR, 0, MS_REMOUNT | MS_RDONLY, 0) < 0) {
            say(CARD_DIR " not remounted read-only: ", strerror(errno));
        }
    }
    sync();
    reboot(how);
    for (;;) {
        pause();
    }
}

int main(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    sigaction(SIGTERM, &sa, 0);
    sigaction(SIGUSR1, &sa, 0);
    sigaction(SIGUSR2, &sa, 0);
    signal(SIGINT, SIG_IGN);
    signal(SIGHUP, SIG_IGN);

    if (mount("devtmpfs", "/dev", "devtmpfs", 0, 0) < 0 && errno != EBUSY) {
        // stderr may not reach the console yet: no message
    }
    console_on_stdio();
    if (mount("proc", "/proc", "proc", 0, 0) < 0) {
        say("/proc not mounted: ", strerror(errno));
    }
    mount_card();

    for (;;) {
        unsigned long started = now_ms();
        pid_t shell = start_shell();
        for (;;) {
            if (stop_signal) {
                stop();
            }
            int status;
            pid_t pid = wait(&status);
            if (pid < 0 && errno == ECHILD) {
                break;
            }
            if (pid == shell) {
                break;
            }
        }
        if (now_ms() - started < 1000) {
            sleep_ms(1000);
        }
    }
}
