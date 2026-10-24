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
endef
endif
