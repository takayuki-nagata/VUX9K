// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// The largest block of contiguous pages the kernel has free, allocated once to show that a
// program can have it (noMMU: an anonymous mmap is one block of pages, alloc_pages_exact).
// The order comes from /proc/buddyinfo rather than from allocating until it fails, which would
// print the kernel's page allocation failure with a memory dump to the console
// (docs/agents/linux.md, "Memory budget"). Prints one line:
//   memprobe: MemFree N kB, largest free M kB (order K), allocated
// and exits 1 when something fails. No stdio: the executable sits in the initramfs (RAM for
// good) and is copied whole at each exec, and printf/scanf alone made it ~120 KB.

#include <fcntl.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define PAGE 4096

static char text[4096];

// The file whole into text (NUL-terminated); its length, or -1
static int slurp(const char *path) {
    int fd = open(path, O_RDONLY), len = 0, n;
    if (fd < 0)
        return -1;
    while (len < (int)sizeof text - 1 && (n = read(fd, text + len, sizeof text - 1 - len)) > 0)
        len += n;
    close(fd);
    text[len] = 0;
    return n < 0 ? -1 : len;
}

static const char *skip_spaces(const char *p) {
    while (*p == ' ' || *p == '\t')
        p++;
    return p;
}

// A decimal number at *pp, advancing past it; -1 if there is none
static long number(const char **pp) {
    const char *p = skip_spaces(*pp);
    long n = 0;
    if (*p < '0' || *p > '9')
        return -1;
    while (*p >= '0' && *p <= '9')
        n = n * 10 + (*p++ - '0');
    *pp = p;
    return n;
}

static long mem_free_kb(void) {
    const char *p;
    if (slurp("/proc/meminfo") < 0 || !(p = strstr(text, "MemFree:")))
        return -1;
    p += strlen("MemFree:");
    return number(&p);
}

// The highest order with a free block, over all zones (`Node 0, zone Normal n0 n1 ...`); -1 if
// none or unreadable
static int largest_order(void) {
    const char *p = text;
    int largest = -1;
    if (slurp("/proc/buddyinfo") < 0)
        return -1;
    while ((p = strstr(p, "zone"))) {
        int order = 0;
        long n;
        p = skip_spaces(p + 4);
        while (*p && *p != ' ' && *p != '\n')  // the zone's name
            p++;
        while ((n = number(&p)) >= 0) {
            if (n > 0 && order > largest)
                largest = order;
            order++;
        }
    }
    return largest;
}

static char line[160];
static int at;

static void put(const char *s) {
    while (*s && at < (int)sizeof line)
        line[at++] = *s++;
}

static void put_num(long n) {
    char digits[12];
    int i = 0;
    do
        digits[i++] = (char)('0' + n % 10);
    while ((n /= 10) && i < (int)sizeof digits);
    while (i && at < (int)sizeof line)
        line[at++] = digits[--i];
}

static int fail(const char *why) {
    at = 0;
    put("memprobe: ");
    put(why);
    put("\n");
    write(2, line, at);
    return 1;
}

int main(void) {
    long free_kb = mem_free_kb();
    int order = largest_order();
    unsigned long size;
    unsigned char *block;

    if (free_kb < 0 || order < 0)
        return fail("cannot read /proc/meminfo or /proc/buddyinfo");
    size = (unsigned long)PAGE << order;
    block = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (block == MAP_FAILED)
        return fail("the largest free block was not allocated");
    memset(block, 0xa5, size);  // every page, so the block is really there
    for (unsigned long i = 0; i < size; i += PAGE)
        if (block[i] != 0xa5 || block[i + PAGE - 1] != 0xa5)
            return fail("the block reads back wrong");
    munmap(block, size);
    put("memprobe: MemFree ");
    put_num(free_kb);
    put(" kB, largest free ");
    put_num((long)(size / 1024));
    put(" kB (order ");
    put_num(order);
    put("), allocated\n");
    return write(1, line, at) == at ? 0 : 1;
}
