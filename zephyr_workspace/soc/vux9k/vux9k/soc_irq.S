/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/toolchain.h>
#include <zephyr/linker/sections.h>

WTEXT(__soc_handle_irq)

SECTION_FUNC(exception.other, __soc_handle_irq)
	ret
