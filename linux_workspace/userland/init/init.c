// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// VUX9K's init (/init in the initramfs, the kernel's first process), in place of busybox's:
// busybox init stayed in RAM as a whole copy of busybox (~370 KB, noMMU FLAT: every process
// has its own copy of its executable), which the kernel with the SD card could not spare.
// This one does without exec what the inittab and buildroot's /init script did:
//   - devtmpfs on /dev (the kernel mounts it only for a root that is not an initramfs), the
//     console on fds 0-2, proc on /proc, sysfs on /sys;
//   - the SD card's FAT32 partition on /mnt/sd, once mmc has found the card (in the
//     background, possibly after init has started: up to CARD_WAIT_MS);
//   - the login shell (-/bin/sh, which reads /etc/profile) on the console, in its own session
//     with the console as its controlling tty, started again when it exits (after a pause if
//     it lived less than a second: an exec that fails would loop);
//   - the system monitor (SYSMON, linux_workspace/userland/sysmon/) beside the shell, in its own
//     session on the console's output; started again RESTART_S later when it dies (a signal
//     other than SIGTERM or SIGINT, or a status other than 0), twice as late each time it
//     dies within a minute (up to RESTART_MAX_S), not when it was stopped (`kill <pid>`: it
//     ends with status 0); a missing SYSMON is skipped;
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
#define SYSMON "/usr/sbin/sysmon"
// The first restart's delay, doubled after each end that came within RESTART_RESET_S of its
// start (an exec that fails, a program that can't run here), up to RESTART_MAX_S
#define RESTART_S 5
#define RESTART_MAX_S 320
#define RESTART_RESET_S 60

static volatile sig_atomic_t stop_signal;
static volatile sig_atomic_t alarm_rang;
// The signal mask init's children start with: init blocks the signals it waits for, so that
// one can't come between its check of their flags and its sleep (sigsuspend)
static sigset_t child_mask;

// One line, in one write: the console interleaves other processes' writes (the shell's prompt)
// between separate ones
static void say(const char *a, const char *b) {
    char line[160];
    size_t n = 0;
    const char *parts[] = {"init: ", a, b ? b : "", "\n"};
    for (size_t i = 0; i < sizeof parts / sizeof parts[0]; i++) {
        size_t len = strlen(parts[i]);
        if (len > sizeof line - 1 - n) {
            len = sizeof line - 1 - n;
        }
        memcpy(line + n, parts[i], len);
        n += len;
    }
    if (line[n - 1] != '\n') {
        line[n - 1] = '\n';
    }
    write(2, line, n);
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
        // init ignores these; the shell and its programs must not (SIG_IGN survives execve,
        // as does the signal mask)
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        sigprocmask(SIG_SETMASK, &child_mask, 0);
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

// The daemon in its own session (no signals from the console's keys), stdin /dev/null, its
// output on the console; -1 if vfork failed, -2 if there is no SYSMON
static pid_t start_sysmon(void) {
    if (access(SYSMON, X_OK) < 0) {
        return -2;
    }
    pid_t pid = vfork();
    if (pid == 0) {
        static char *const argv[] = {"sysmon", 0};
        static char *const envp[] = {"PATH=/sbin:/usr/sbin:/bin:/usr/bin", 0};
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        sigprocmask(SIG_SETMASK, &child_mask, 0);
        setsid();
        int fd = open("/dev/null", O_RDONLY);
        if (fd > 0) {
            dup2(fd, 0);
            close(fd);
        }
        execve(SYSMON, argv, envp);
        write(2, "init: cannot run " SYSMON "\n", sizeof "init: cannot run " SYSMON "\n" - 1);
        _exit(127);
    }
    if (pid < 0) {
        say("cannot start " SYSMON ": ", strerror(errno));
    }
    return pid;
}

// n in decimal, in buf (12 bytes)
static const char *decimal(char *buf, unsigned n) {
    char *p = buf + 11;
    *p = 0;
    do {
        *--p = (char)('0' + n % 10);
        n /= 10;
    } while (n);
    return p;
}

// The daemon's end: whether init starts it again (it died, or its exec failed)
static int sysmon_died(int status) {
    char num[12];
    int n = WIFSIGNALED(status) ? WTERMSIG(status) : WEXITSTATUS(status);
    if (WIFEXITED(status) && n == 0) {
        say(SYSMON " stopped", 0);
        return 0;
    }
    say(WIFSIGNALED(status) ? SYSMON " killed by signal " : SYSMON " exited with status ",
        decimal(num, (unsigned)n));
    return 1;
}

// Restarts the daemon after delay seconds: its message, and the alarm that ends init's wait
static void restart_sysmon_in(unsigned delay) {
    char line[48] = "restarting " SYSMON " in ", num[12];
    size_t n = strlen(line);
    const char *d = decimal(num, delay);
    memcpy(line + n, d, strlen(d));
    memcpy(line + n + strlen(d), " s", 3);
    say(line, 0);
    alarm(delay);
}

static void on_child(int sig) {
    (void)sig;
}

static void on_stop(int sig) {
    stop_signal = sig;
}

static void on_alarm(int sig) {
    (void)sig;
    alarm_rang = 1;
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
    sa.sa_handler = on_alarm;
    sigaction(SIGALRM, &sa, 0);
    // A handler, so that a child's end ends sigsuspend (SIGCHLD's default is to ignore it)
    sa.sa_handler = on_child;
    sigaction(SIGCHLD, &sa, 0);
    signal(SIGINT, SIG_IGN);
    signal(SIGHUP, SIG_IGN);

    if (mount("devtmpfs", "/dev", "devtmpfs", 0, 0) < 0 && errno != EBUSY) {
        // stderr may not reach the console yet: no message
    }
    console_on_stdio();
    if (mount("proc", "/proc", "proc", 0, 0) < 0) {
        say("/proc not mounted: ", strerror(errno));
    }
    // The LED class and the GPIO chips' names (sysmon, the shell's echo > .../brightness)
    if (mount("sysfs", "/sys", "sysfs", 0, 0) < 0) {
        say("/sys not mounted: ", strerror(errno));
    }
    mount_card();

    // The signals init waits for stay blocked but in sigsuspend, which takes them atomically:
    // a flag is checked, then init sleeps, and a signal between the two still wakes it
    sigset_t waited;
    sigemptyset(&waited);
    sigaddset(&waited, SIGTERM);
    sigaddset(&waited, SIGUSR1);
    sigaddset(&waited, SIGUSR2);
    sigaddset(&waited, SIGALRM);
    sigaddset(&waited, SIGCHLD);
    sigprocmask(SIG_BLOCK, &waited, &child_mask);

    unsigned delay = RESTART_S;
    unsigned long sysmon_started = now_ms();
    pid_t sysmon = start_sysmon();
    if (sysmon == -1) {
        restart_sysmon_in(delay);
    }
    for (;;) {
        unsigned long started = now_ms();
        pid_t shell = start_shell();
        for (;;) {
            if (stop_signal) {
                stop();
            }
            if (alarm_rang) {
                alarm_rang = 0;
                sysmon_started = now_ms();
                sysmon = start_sysmon();
                if (sysmon == -1) {
                    restart_sysmon_in(delay);
                }
            }
            int status;
            pid_t pid = waitpid(-1, &status, WNOHANG);
            if (pid == 0) {
                sigsuspend(&child_mask);
                continue;
            }
            if (pid < 0) {
                if (errno == ECHILD) {
                    break;
                }
                continue;
            }
            if (pid == shell) {
                break;
            }
            if (pid == sysmon) {
                sysmon = -1;
                if (sysmon_died(status)) {
                    if (now_ms() - sysmon_started >= RESTART_RESET_S * 1000UL) {
                        delay = RESTART_S;
                    }
                    restart_sysmon_in(delay);
                    delay = delay * 2 > RESTART_MAX_S ? RESTART_MAX_S : delay * 2;
                }
            }
        }
        if (now_ms() - started < 1000) {
            sleep_ms(1000);
        }
    }
}
