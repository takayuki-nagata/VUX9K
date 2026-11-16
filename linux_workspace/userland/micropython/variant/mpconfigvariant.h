// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MicroPython's unix port for VUX9K's noMMU Linux (package vux9k-micropython: a FLAT program
// on the card). The extra features, as the Zephyr build has them
// (zephyr_workspace/micropython/prj.conf): bc_clone_py needs f-strings, re, math.factorial
// and sys.stdin's files, and the unix features of the standard variant (arbitrary-precision
// integers, double floats, os, the posix VFS). Included by the port's mpconfigport.h before
// anything else, so what is set here wins over its defaults.

#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES)

// The standard variant's unix features (from ports/unix, the include path's ".")
#include "variants/mpconfigvariant_common.h"

// No native code: mpconfigport.h turns the RV32 emitter on for any riscv32, and the port then
// needs an executable mmap for its code, which a FLAT process doesn't get
#define MICROPY_EMIT_RV32 (0)

// Nothing here for a websocket to talk to (no network)
#undef MICROPY_PY_WEBSOCKET
#define MICROPY_PY_WEBSOCKET (0)

// The heap: noMMU's malloc of N bytes takes one contiguous block of a power of two pages, so
// the port's 1 MB heap would need the largest block there is. A small heap first
// (MICROPY_UNIX_HEAP_SIZE, patch 0002; -X heapsize still sets it), then another area each
// time a collection leaves no room (gc_get_max_new_split in vux9k_heap.c: at most an order-5
// block, 128 KB, each time, so the heap leaves the larger blocks to the execs)
#define MICROPY_GC_SPLIT_HEAP (1)
#define MICROPY_GC_SPLIT_HEAP_AUTO (1)
#define MICROPY_UNIX_HEAP_SIZE (64 * 1024)
// The new areas come from malloc and go back to free (py/mpconfig.h's MP_PLAT_ALLOC_HEAP and
// MP_PLAT_FREE_HEAP), which py/gc.c calls without stdlib.h
#include <stdlib.h>

// Where `import` looks after the script's directory (or "" for -c and the REPL) when MICROPYPATH
// is not set: the card's modules (VUX9K/LINUX/PYLIB.TAR, which the login shell's profile
// extracts there: linux_workspace/userland/scripts/pylib). The profile sets MICROPYPATH to the
// same; this default is for a program not started from the login shell. Nothing is frozen, so
// no ".frozen" (the unix port's default path is ".frozen:~/.micropython/lib:/usr/lib/micropython")
#define MICROPY_PY_SYS_PATH_DEFAULT "/mnt/sd/VUX9K/LINUX/PYLIB"

// The C stack the recursion check allows (patch 0001): below the FLAT stack elf2flt gives the
// program (VUX9K_MICROPYTHON_FLAT_STACKSIZE, 0x6000 = 24,576 B), with room for the frames
// above main() and the arguments and environment the kernel puts on the same stack
#define MICROPY_UNIX_STACK_LIMIT (20000)
