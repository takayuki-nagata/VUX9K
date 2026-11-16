// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// The largest new heap area MICROPY_GC_SPLIT_HEAP_AUTO may ask malloc for: on noMMU an
// allocation is one block of contiguous pages, so the largest free block in /proc/buddyinfo,
// less a page for malloc's own header, and never more than an order-5 block's worth
// (VUX9K_SPLIT_MAX). py/gc.c grows the heap by its own size each time, up to what this allows:
// uncapped, a heap of 256 KB would take an order-7 block, the one MPY's own exec needs
// (docs/agents/linux.md, "MicroPython"), and break the large blocks every program's exec
// needs. Capped, it grows 64 KB -> ~130 KB -> ~254 KB -> ... in areas of at most 124 KB, each
// in an order-5 block, of which the free memory has many. The price: a single object can't be
// larger than an area (a list of 30,000 items, a bytes of 124 KB). The port's build compiles
// every .c of its variant.

#include <fcntl.h>
#include <unistd.h>

#include "py/gc.h"

#if MICROPY_GC_SPLIT_HEAP_AUTO

#define VUX9K_PAGE (4096)
// An order-5 block (128 KB) less malloc's page
#define VUX9K_SPLIT_ORDER (5)
#define VUX9K_SPLIT_MAX (((size_t)VUX9K_PAGE << VUX9K_SPLIT_ORDER) - VUX9K_PAGE)

size_t gc_get_max_new_split(void) {
    char buf[512];
    // Without /proc, or unreadable: the cap, and malloc says whether it fits
    int fd = open("/proc/buddyinfo", O_RDONLY);
    if (fd < 0) {
        return VUX9K_SPLIT_MAX;
    }
    ssize_t len = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (len <= 0) {
        return VUX9K_SPLIT_MAX;
    }
    buf[len] = 0;
    // Each line: "Node 0, zone   Normal  c0 c1 c2 ...", the free blocks of each order
    int largest = -1;
    int counts = 0;
    for (const char *p = buf; *p;) {
        const char *zone = p;
        while (*p && *p != '\n') {
            p++;
        }
        const char *end = p;
        if (*p) {
            p++;
        }
        // The counts come after the two words that follow ',' (zone and its name)
        while (zone < end && *zone != ',') {
            zone++;
        }
        int words = 0;
        while (zone < end && words < 2) {
            while (zone < end && (*zone == ',' || *zone == ' ')) {
                zone++;
            }
            while (zone < end && *zone != ' ') {
                zone++;
            }
            words++;
        }
        for (int order = 0; zone < end; order++) {
            while (zone < end && *zone == ' ') {
                zone++;
            }
            if (zone >= end || *zone < '0' || *zone > '9') {
                break;
            }
            unsigned long count = 0;
            counts++;
            while (zone < end && *zone >= '0' && *zone <= '9') {
                count = count * 10 + (unsigned long)(*zone++ - '0');
            }
            if (count && order > largest) {
                largest = order;
            }
        }
    }
    // No count read at all (a format this doesn't know): the cap, as without the file
    if (!counts) {
        return VUX9K_SPLIT_MAX;
    }
    // Counts, all of them 0: no free block
    if (largest < 0) {
        return 0;
    }
    if (largest >= VUX9K_SPLIT_ORDER) {
        return VUX9K_SPLIT_MAX;
    }
    return ((size_t)VUX9K_PAGE << largest) - VUX9K_PAGE;
}

#endif
