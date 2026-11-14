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
//     other than SIGTERM or SIGINT, or a status other than 0), not when it was stopped
//     (`kill <pid>`: it ends with status 0); a missing SYSMON is skipped;
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
#define RESTART_S 5
#define TEXT(x) #x
#define NUMBER(x) TEXT(x)

static volatile sig_atomic_t stop_signal;
static volatile sig_atomic_t alarm_rang;

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

// The daemon in its own session (no signals from the console's keys), stdin /dev/null, its
// output on the console; -1 if it can't be started
static pid_t start_sysmon(void) {
    if (access(SYSMON, X_OK) < 0) {
        return -1;
    }
    pid_t pid = vfork();
    if (pid == 0) {
        static char *const argv[] = {"sysmon", 0};
        static char *const envp[] = {"PATH=/sbin:/usr/sbin:/bin:/usr/bin", 0};
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
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

// The daemon's end: whether init starts it again (it died, or its exec failed)
static int sysmon_died(int status) {
    static char num[12];
    char *p = num + sizeof num - 1;
    int n = WIFSIGNALED(status) ? WTERMSIG(status) : WEXITSTATUS(status);
    if (WIFEXITED(status) && n == 0) {
        say(SYSMON " stopped", 0);
        return 0;
    }
    *p = 0;
    do {
        *--p = (char)('0' + n % 10);
        n /= 10;
    } while (n);
    say(WIFSIGNALED(status) ? SYSMON " killed by signal " : SYSMON " exited with status ", p);
    say("restarting " SYSMON " in " NUMBER(RESTART_S) " s", 0);
    return 1;
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
    // No SA_RESTART: the alarm ends init's wait for its children
    sa.sa_handler = on_alarm;
    sigaction(SIGALRM, &sa, 0);
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

    pid_t sysmon = start_sysmon();
    for (;;) {
        unsigned long started = now_ms();
        pid_t shell = start_shell();
        for (;;) {
            if (stop_signal) {
                stop();
            }
            if (alarm_rang) {
                alarm_rang = 0;
                sysmon = start_sysmon();
            }
            int status;
            pid_t pid = wait(&status);
            if (pid < 0 && errno == ECHILD) {
                break;
            }
            if (pid == shell) {
                break;
            }
            if (pid > 0 && pid == sysmon) {
                sysmon = -1;
                if (sysmon_died(status)) {
                    alarm(RESTART_S);
                }
            }
        }
        if (now_ms() - started < 1000) {
            sleep_ms(1000);
        }
    }
}
