# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: Apache-2.0

zephyr_sources(soc_irq.S)
zephyr_include_directories(.)

set(SOC_LINKER_SCRIPT ${ZEPHYR_BASE}/include/zephyr/arch/riscv/common/linker.ld CACHE INTERNAL "")
