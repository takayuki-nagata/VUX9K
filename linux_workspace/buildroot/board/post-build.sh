#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# buildroot's post-build script, $1 the target directory. busybox init's table, in place of the
# skeleton's: every line there is a process, and each exec copies busybox into contiguous RAM
# (noMMU, FLAT). /proc, then the shell on the console (the UART), started again when it exits.
set -eu
cat > "$1/etc/inittab" <<'TAB'
::sysinit:/bin/mount -t proc proc /proc
console::respawn:-/bin/sh
TAB
