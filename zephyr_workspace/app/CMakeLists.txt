# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(vux9k_zephyr_rust)

# The Rust part (rust_demo/) is a staticlib for RV32I, built by cargo into this
# build directory; cargo itself decides whether anything changed (ALL + always run).
set(RUST_TARGET riscv32i-unknown-none-elf)
set(RUST_TARGET_DIR ${CMAKE_BINARY_DIR}/rust_demo)
set(RUST_LIB ${RUST_TARGET_DIR}/${RUST_TARGET}/release/libvux9k_rust_demo.a)

add_custom_target(rust_demo_staticlib
    COMMAND ${CMAKE_COMMAND} -E env CARGO_TARGET_DIR=${RUST_TARGET_DIR}
            cargo build --release --target ${RUST_TARGET}
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/rust_demo
    BYPRODUCTS ${RUST_LIB}
    COMMENT "Building the rust_demo staticlib for RV32I"
)

target_sources(app PRIVATE src/main.c)
add_dependencies(app rust_demo_staticlib)
target_link_libraries(app PRIVATE ${RUST_LIB})
