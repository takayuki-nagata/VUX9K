# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# Built by buildroot's package vux9k-memprobe (linux_workspace/buildroot/package/), which
# passes its toolchain's CC, CFLAGS and LDFLAGS (FLAT: elf2flt's flags are in them)
memprobe: memprobe.c
	$(CC) $(CFLAGS) -Wall -Wextra -o $@ $< $(LDFLAGS)

clean:
	rm -f memprobe memprobe.gdb

.PHONY: clean
