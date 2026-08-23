# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

BUILD_DIR ?= ../build_hack
HAS ?= $(HOME)/.local/bin/has
BIN2HEX ?= python3 ../scripts/bin2hex.py

all: $(BUILD_DIR)/firmware.hex

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/firmware.bin: src/main.asm | $(BUILD_DIR)
	@echo "=== Assembling Hack Firmware with has ==="
	$(HAS) src/main.asm -r -o $(BUILD_DIR)/firmware.bin

$(BUILD_DIR)/firmware.hex: $(BUILD_DIR)/firmware.bin | $(BUILD_DIR)
	@echo "=== Generating 32-bit Hex Word ROM for SoC ==="
	$(BIN2HEX) $(BUILD_DIR)/firmware.bin $(BUILD_DIR)/firmware.hex

clean:
	rm -rf $(BUILD_DIR)

.PHONY: all clean
