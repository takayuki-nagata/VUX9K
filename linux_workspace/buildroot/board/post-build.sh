#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# buildroot's post-build script, $1 the target directory: busybox init starts the shell on the
# console (the UART) and starts it again when it exits
set -eu
inittab="$1/etc/inittab"
grep -q '^console::respawn:-/bin/sh$' "$inittab" || echo 'console::respawn:-/bin/sh' >> "$inittab"
