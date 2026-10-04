# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Shared helpers of the Claude Code hooks in this directory (wired up in .claude/settings.json).

Every check here is one `make check` also runs (or a syntax-only part of the RTL flow); the hooks
just run the relevant ones right after an edit and when an agent stops, so mistakes come back
while the change is still in front of it.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Python the repo's ruff/mypy configuration covers (pyproject.toml)
PY_DIRS = ("sim/", "tools/", "scripts/", ".claude/hooks/")
# Rust crates `make check`/`emu-test` format, by directory
CARGO_DIRS = ("firmware/", "emu/")
MAX_LINES = 40


def read_input() -> dict:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def rel_path(path: str | None) -> str | None:
    """`path` relative to the repo root (POSIX separators), or None when it's outside it."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def tool(name: str) -> str:
    """The repo's virtualenv copy of a Python tool when there is one (as `make setup` installs it)."""
    venv = ROOT / ".venv" / "bin" / name
    if venv.exists():
        return str(venv)
    return shutil.which(name) or name


def is_python(rel: str) -> bool:
    return rel.endswith(".py") and rel.startswith(PY_DIRS)


def run(cmd: list[str], cwd: Path = ROOT, timeout: float = 120) -> tuple[bool, str]:
    """Runs `cmd`; returns (passed, combined output). A timeout or a missing tool counts as passed,
    with a note: the hooks must never block an agent because the machine is slow or a tool absent."""
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return True, f"(skipped: `{' '.join(cmd)}` took over {timeout:.0f} s)"
    except FileNotFoundError:
        return True, f"(skipped: `{cmd[0]}` not found)"
    return res.returncode == 0, (res.stdout + res.stderr).strip()


# Progress and known-harmless lines of the tools above (veryl build's file list, Icarus' notes on
# Veryl's always_comb output) that would otherwise push the actual error out of view
NOISE = re.compile(r"^\[INFO |: sorry: constant selects|: warning: always_comb process has no sensitivities")


def clip(text: str, lines: int = MAX_LINES) -> str:
    """The output without NOISE, cut to its first and last lines (make puts its error last)."""
    out = [line for line in text.splitlines() if not NOISE.search(line)]
    if len(out) <= lines:
        return "\n".join(out)
    head = lines // 4
    tail = lines - head
    return "\n".join([*out[:head], f"... ({len(out) - lines} lines omitted)", *out[-tail:]])


def block(failures: list[tuple[str, str]]) -> None:
    """Reports failed checks to the agent (exit 2: stderr goes back to it) and exits."""
    parts = [f"$ {name}\n{clip(output)}" for name, output in failures]
    print("Checks failed; fix these before continuing:\n\n" + "\n\n".join(parts), file=sys.stderr)
    sys.exit(2)
