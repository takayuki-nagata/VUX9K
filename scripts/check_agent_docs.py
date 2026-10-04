#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Checks that the agent guides (AGENTS.md, docs/agents/, .claude/rules/) still match the tree.

- Every source-tree path the guides name in backticks or links exists (paths that start with a
  top-level entry of the repository; build/, placeholders and globs are skipped).
- Every citation `docs/agents/<file>.md, "<Title>"` (in code or in the guides) names a guide
  that exists and a section whose heading starts with that title.
- Every guide has a .claude/rules/ entry and every rule imports a guide that exists.

Renames and section edits otherwise leave the guides pointing at nothing, silently.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDES = ROOT / "docs" / "agents"
RULES = ROOT / ".claude" / "rules"
PATH_EXT = re.compile(r"\.(py|veryl|sv|rs|md|toml|yml|yaml|ld|S|h|vlt|cst|x|sh|tsv)$")
# Paths the guides name on purpose although a fresh checkout doesn't have them: fetched on
# demand (riscv-tests), or local files the guides tell readers about
NOT_TRACKED = ("vendor/riscv-tests", "firmware/target/", "firmware.hex", "firmware_d0-3.hex")
# In code a citation names the guide with its directory; inside the guides the file name is enough
CITATION_CODE = re.compile(r"docs/agents/([a-z0-9-]+\.md),\s*\"([^\"]+)\"")
CITATION_GUIDE = re.compile(r"(?<![\w/])([a-z0-9-]+\.md),\s*\"([^\"]+)\"")
ROOT_MD = re.compile(r"\[[^\]]*\]\(([^)#]+)\)")


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def guide_files() -> list[Path]:
    return [ROOT / "AGENTS.md", *sorted(GUIDES.glob("*.md"))]


def headings(path: Path) -> list[str]:
    return [line.lstrip("#").strip() for line in path.read_text().splitlines() if line.startswith("#")]


def looks_like_path(token: str, top: set[str]) -> bool:
    """A path into the source tree: it starts with a top-level entry of the repository. Bare file
    names and paths inside generated or upstream trees (run directories, the riscv-tests env,
    board names like vux9k/vux9k/ext) are too ambiguous to check."""
    if any(c in token for c in " <>*{}$~|=") or token.startswith(("/", "http", "0x", "-")):
        return False
    first = token.split("/")[0].split(":")[0]
    return first in top and ("/" in token or bool(PATH_EXT.search(token)) or (ROOT / first).is_dir())


def path_exists(token: str, files: list[str]) -> bool:
    token = token.rstrip("/").split(":")[0]
    if token.startswith(NOT_TRACKED) or (ROOT / token).exists():
        return True
    return any(f.startswith(token + "/") for f in files)


def check_paths(errors: list[str]) -> None:
    files = tracked()
    top = {f.split("/")[0] for f in files} - {"build"}
    for doc in guide_files():
        text = doc.read_text()
        tokens = set(re.findall(r"`([^`\n]+)`", text))
        links = {t for t in ROOT_MD.findall(text) if not t.startswith("http")}
        for token in sorted(tokens):
            if looks_like_path(token, top) and not path_exists(token, files):
                errors.append(f"{doc.relative_to(ROOT)}: `{token}` doesn't exist")
        for link in sorted(links):
            if not (doc.parent / link).resolve().exists():
                errors.append(f"{doc.relative_to(ROOT)}: link {link} doesn't exist")


def flatten_comments(text: str) -> str:
    """Joins comment continuation lines, so a citation broken over two comment lines still matches."""
    return re.sub(r"\s*\n\s*(?:#|//|\*)?\s*", " ", text)


def check_citations(errors: list[str]) -> None:
    for rel in tracked():
        src = ROOT / rel
        if rel.startswith("vendor/") or not src.is_file():
            continue
        try:
            text = flatten_comments(src.read_text())
        except UnicodeDecodeError:
            continue
        in_guides = src.parent == GUIDES or rel == "AGENTS.md"
        for name, title in (CITATION_GUIDE if in_guides else CITATION_CODE).findall(text):
            guide = GUIDES / name
            if not guide.exists():
                errors.append(f"{rel}: cites missing guide {name}")
            elif not any(h.startswith(title) for h in headings(guide)):
                errors.append(f'{rel}: {name} has no section "{title}"')


def check_rules(errors: list[str]) -> None:
    imported = set()
    for rule in sorted(RULES.glob("*.md")):
        for target in re.findall(r"^@(\S+)", rule.read_text(), re.M):
            path = (rule.parent / target).resolve()
            if not path.exists():
                errors.append(f"{rule.relative_to(ROOT)}: imports missing {target}")
            imported.add(path)
    for guide in sorted(GUIDES.glob("*.md")):
        if guide.resolve() not in imported:
            errors.append(f"{guide.relative_to(ROOT)}: no .claude/rules/ entry loads it")


def main() -> int:
    errors: list[str] = []
    check_paths(errors)
    check_citations(errors)
    check_rules(errors)
    for e in errors:
        print(f"❌ {e}")
    if errors:
        return 1
    print("✅ [PASS] Agent guides: paths, citations and rules are consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
