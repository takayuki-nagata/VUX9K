# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# MicroPython's unix port for VUX9K's noMMU Linux (package vux9k-micropython): what the
# toolchain has (no threads, no libffi, no network) and what the card's program needs
# (MicroPython's own readline for the REPL, the posix VFS only)

# Nothing frozen in (docs/agents/linux.md, "MicroPython"): every exec copies the whole program
# into one block of contiguous RAM, so a module the program carries costs RAM at each run and
# a larger block. The port's Makefile would otherwise take variants/manifest.py (asyncio, mip,
# ssl, unix-ffi); defined empty here, its `?=` leaves it so. bc_clone_py's modules are .mpy
# files on the card (VUX9K/LINUX/PYLIB.TAR, extracted into VUX9K/LINUX/PYLIB/)
FROZEN_MANIFEST =

MICROPY_PY_BTREE = 0
MICROPY_PY_FFI = 0
MICROPY_PY_SOCKET = 0
MICROPY_PY_THREAD = 0
MICROPY_PY_TERMIOS = 0
MICROPY_PY_SSL = 0
MICROPY_USE_READLINE = 1

MICROPY_VFS_FAT = 0
MICROPY_VFS_LFS1 = 0
MICROPY_VFS_LFS2 = 0
