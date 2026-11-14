# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# buildroot sets BR2_SOFT_FLOAT for no RISC-V target, so a riscv32 without F gets uClibc's FPU
# configuration, whose riscv32 fenv (libm/riscv32/) needs F (`frrm`: the build stops). Included
# after package/uclibc/uclibc.mk, this replaces that configuration with its soft-float one, which
# uClibc's configuration fixups expand only when they run.
ifeq ($(BR2_riscv):$(BR2_RISCV_ISA_RVF),y:)
define UCLIBC_FLOAT_CONFIG
	$(call KCONFIG_DISABLE_OPT,UCLIBC_HAS_FPU)
	$(call KCONFIG_DISABLE_OPT,UCLIBC_HAS_FENV)
	$(call KCONFIG_ENABLE_OPT,UCLIBC_HAS_FLOATS)
	$(call KCONFIG_ENABLE_OPT,DO_C99_MATH)
	$(call KCONFIG_DISABLE_OPT,UCLIBC_HAS_LONG_DOUBLE_MATH)
endef
endif

# Size: every program is linked statically and copied whole into contiguous RAM at each exec,
# so what libc puts into all of them counts (docs/agents/linux.md, "Memory budget"). Without
# long double math, board/patches/uclibc/ makes printf's and strtod's arithmetic double: the
# soft-float 128-bit long double routines were ~22 KB of busybox. And no unwind tables: nothing
# unwinds C here (no threads, no C++), and libc.a's .eh_frame was ~19 KB of busybox's data.
UCLIBC_EXTRA_CFLAGS += -fno-asynchronous-unwind-tables -fno-unwind-tables

# This tree's own packages
include $(sort $(wildcard $(BR2_EXTERNAL_VUX9K_PATH)/package/*/*.mk))
