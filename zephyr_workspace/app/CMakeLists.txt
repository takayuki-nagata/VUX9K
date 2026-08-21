# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(vux9k_zephyr_rust)

set(RUST_TARGET riscv32i-unknown-none-elf)
set(RUST_LIB_DIR ${CMAKE_CURRENT_SOURCE_DIR}/rust_app/target/${RUST_TARGET}/release)
set(RUST_LIB ${RUST_LIB_DIR}/libvux9k_rust_app.a)

# Custom command to automatically build Rust staticlib during Zephyr build
add_custom_command(
    OUTPUT ${RUST_LIB}
    COMMAND cargo build --release --target ${RUST_TARGET}
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/rust_app
    COMMENT "Building Rust staticlib for Zephyr RV32I..."
)

add_custom_target(rust_app_staticlib DEPENDS ${RUST_LIB})

target_sources(app PRIVATE src/main.c)
add_dependencies(app rust_app_staticlib)
target_link_libraries(app PRIVATE ${RUST_LIB})
