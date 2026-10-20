# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: Apache-2.0

# MicroPython's frozen modules for vux9k (make build-micropython passes this file as
# CONFIG_MICROPY_FROZEN_MANIFEST): bc_clone_py's modules, from the commit `make
# micropython-src` fetches into vendor/bc_clone_py. Paths are relative to this file.
# On the board: `import bc_selftest; bc_selftest.run()`, `import bc_clone; bc_clone.repl(mathlib=True)`.
for name in ("bc_clone", "bc_eval", "bc_math", "bc_parser", "bc_selftest"):
    module(name + ".py", base_path="../../vendor/bc_clone_py")
