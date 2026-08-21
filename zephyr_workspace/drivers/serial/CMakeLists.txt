# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: Apache-2.0

zephyr_library()
zephyr_library_sources_ifdef(CONFIG_UART_VUX9K uart_vux9k.c)
