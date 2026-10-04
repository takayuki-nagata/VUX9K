#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""PostToolUse hook (Edit/Write): lint and type-check the file just written, without changing it.

Python: ruff check on the file, then mypy (the whole configured tree; cached, well under a second).
Veryl: veryl check. Formatting and the slower checks wait for the Stop hook (stop_check.py).
"""

from hooklib import block, is_python, read_input, rel_path, run, tool


def main() -> None:
    data = read_input()
    rel = rel_path((data.get("tool_input") or {}).get("file_path"))
    if rel is None:
        return
    failures = []
    if is_python(rel):
        ok, out = run([tool("ruff"), "check", "--quiet", rel], timeout=30)
        if not ok:
            failures.append((f"ruff check {rel}", out))
        ok, out = run([tool("mypy")], timeout=60)
        if not ok:
            failures.append(("mypy", out))
    elif rel.endswith(".veryl"):
        ok, out = run(["veryl", "check", "--quiet"], timeout=60)
        if not ok:
            failures.append(("veryl check", out))
    if failures:
        block(failures)


if __name__ == "__main__":
    main()
