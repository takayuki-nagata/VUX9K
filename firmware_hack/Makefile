# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

BUILD_DIR ?= ../build_hack
HAS ?= $(HOME)/.local/bin/has
HCC ?= $(CURDIR)/../.venv/bin/hcc
BIN2HEX ?= python3 ../scripts/bin2hex.py

all: $(BUILD_DIR)/firmware.hex

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/firmware.bin: src/main.c src/main.asm | $(BUILD_DIR)
	@if command -v msp430-gcc >/dev/null 2>&1 && [ -f "$(HCC)" ]; then \
		echo "=== Compiling Hack C Firmware with hcc (msp430-gcc + m2h + has) ==="; \
		$(HCC) -r src/main.c -o $(BUILD_DIR)/firmware.bin; \
	else \
		echo "=== Assembling Hack Firmware with has ==="; \
		$(HAS) src/main.asm -r -o $(BUILD_DIR)/firmware.bin; \
	fi

$(BUILD_DIR)/firmware.hex: $(BUILD_DIR)/firmware.bin | $(BUILD_DIR)
	@echo "=== Generating 32-bit Hex Word ROM for SoC ==="
	$(BIN2HEX) $(BUILD_DIR)/firmware.bin $(BUILD_DIR)/firmware.hex

transpile: src/main.c
	$(HCC) -S src/main.c -o src/main.asm

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean transpile
