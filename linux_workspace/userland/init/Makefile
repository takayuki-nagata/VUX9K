# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# Built by buildroot's package vux9k-init (linux_workspace/buildroot/package/), which passes
# its toolchain's CC, CFLAGS and LDFLAGS (FLAT: elf2flt's flags are in them)
init: init.c
	$(CC) $(CFLAGS) -Wall -Wextra -o $@ $< $(LDFLAGS)

clean:
	rm -f init init.gdb

.PHONY: clean
