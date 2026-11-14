# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# Built by buildroot's package vux9k-sysmon, which passes its toolchain's CC, CFLAGS and LDFLAGS
# (FLAT: -fPIC and elf2flt's flags). The Rust part (src/) is a no_std staticlib for the bare
# RV32IM target, built PIC as that C is (.cargo/config.toml), and linked with main.c by gcc;
# its host tests are make test-sysmon-host at the repository's top.
RUST_TARGET = riscv32im-unknown-none-elf
LIB = target/$(RUST_TARGET)/release/libsysmon.a

sysmon: main.c FORCE
	cargo build --release --target $(RUST_TARGET)
	$(CC) $(CFLAGS) -Wall -Wextra -o $@ main.c $(LIB) $(LDFLAGS)

clean:
	rm -rf sysmon sysmon.gdb target

FORCE:
.PHONY: clean FORCE
