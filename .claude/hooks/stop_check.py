#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Stop/SubagentStop hook: format the changed files, then run the checks that apply to them.

Runs only when the working tree changed since the last clean pass (a turn that only answered a
question costs one `git status`). Formatting happens here rather than after every edit, so files
don't change under an agent mid-edit. A failure blocks the stop (exit 2) and goes back to the
agent; `stop_hook_active` (set when it is already fixing a block) lets it stop the second time, so
this can never loop. Checks stay local and light (the machine may be small and shared by several
sessions): one lock across worktrees, nice'd, a time budget, and a timeout counts as a pass. The
heavy suites (test-sim, clippy, eqy, ...) are CI's job.
"""

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from hooklib import CARGO_DIRS, ROOT, block, is_python, read_input, run, tool

STATE = ROOT / "build" / "agent-hooks" / "stop_ok"
LOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "vux9k-agent-hooks.lock"
BUDGET_S = 90
RTL_SYNTAX_LABEL = 'make check-rtl-syntax  # see AGENTS.md, "Veryl constructs the toolchain rejects"'


def changed_files() -> list[str]:
    """Modified, added and untracked (not ignored) paths, relative to the root; deletions dropped."""
    res = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    fields = res.stdout.split("\0")
    paths = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            i += 1  # the rename's source path follows
        if (ROOT / path).is_file():
            paths.append(path)
    return sorted(paths)


def tree_key(paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.encode() + b"\0" + hashlib.sha256((ROOT / p).read_bytes()).digest())
    return h.hexdigest()


def plan(paths: list[str]) -> tuple[list[tuple[str, list[str], Path]], list[tuple[str, list[str], Path]]]:
    """(formatters, checks) for the changed paths, each as (label, command, cwd)."""
    py = [p for p in paths if is_python(p)]
    veryl = any(p.endswith(".veryl") for p in paths)
    rust = {d for d in CARGO_DIRS for p in paths if p.startswith(d) and p.endswith((".rs", ".toml"))}
    fw_crates = sorted(
        {
            p.split("/")[1]
            for p in paths
            if p.startswith("firmware/") and p.count("/") >= 2 and (ROOT / "firmware" / p.split("/")[1]).is_dir()
        }
    )
    fmt: list[tuple[str, list[str], Path]] = []
    checks: list[tuple[str, list[str], Path]] = []
    if veryl:
        fmt.append(("veryl fmt", ["veryl", "fmt", "--quiet"], ROOT))
    if py:
        fmt.append(("ruff format", [tool("ruff"), "format", "--quiet", *py], ROOT))
    for d in sorted(rust):
        fmt.append((f"cargo fmt ({d})", ["cargo", "fmt"], ROOT / d))
    if py:
        checks.append(("ruff check", [tool("ruff"), "check", "--quiet", *py], ROOT))
        checks.append(("mypy", [tool("mypy")], ROOT))
    if veryl:
        checks.append(("veryl check", ["veryl", "check", "--quiet"], ROOT))
        checks.append(
            (
                'make check-rtl-syntax  # see AGENTS.md, "Veryl constructs the toolchain rejects"',
                ["make", "-s", "check-rtl-syntax"],
                ROOT,
            )
        )
        checks.append(("make lint-rtl", ["make", "-s", "lint-rtl"], ROOT))
    for crate in fw_crates:
        checks.append((f"cargo check (firmware/{crate})", ["cargo", "check", "--quiet"], ROOT / "firmware" / crate))
    if "emu/" in rust:
        checks.append(("cargo check (emu)", ["cargo", "check", "--quiet", "--workspace"], ROOT / "emu"))
    if paths:
        checks.append(("check_no_absolute_paths", ["python3", "scripts/check_no_absolute_paths.py"], ROOT))
    return fmt, checks


def main() -> None:
    data = read_input()
    if data.get("stop_hook_active"):
        return
    paths = changed_files()
    key = tree_key(paths)
    if not paths or (STATE.exists() and STATE.read_text().strip() == key):
        return

    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.nice(10)
        start = time.monotonic()
        fmt, checks = plan(paths)
        failures = []
        notes = []
        for label, cmd, cwd in fmt + checks:
            left = BUDGET_S - (time.monotonic() - start)
            if left <= 5:
                notes.append(f"(skipped {label}: over the {BUDGET_S} s budget)")
                continue
            ok, out = run(cmd, cwd=cwd, timeout=left)
            if not ok:
                failures.append((label, out))
            elif out.startswith("(skipped"):
                notes.append(out)

    if failures:
        block(failures)
    if notes:
        # Something didn't run: don't record this tree as clean, and tell the user why.
        print(json.dumps({"systemMessage": "stop_check: " + "; ".join(notes)}))
        return
    STATE.write_text(tree_key(changed_files()) + "\n")


if __name__ == "__main__":
    main()
