#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""PreToolUse hook (Edit/Write): refuse to write generated or vendored files.

Everything under build/ is regenerated (AGENTS.md, "build/"); the repo-root firmware*.hex are
symlinks into it; target/ is cargo's; vendor/ holds a submodule and fetched upstream code. An edit
there is lost on the next build or hides a bug in the real source, so point at the source instead.
"""

import json
from pathlib import PurePosixPath

from hooklib import read_input, rel_path


def reason_for(rel: str) -> str | None:
    parts = PurePosixPath(rel).parts
    if not parts:
        return None
    if parts[0] == "build":
        return "build/ is generated output: edit the source it comes from (e.g. soc/*.veryl, not build/veryl/*.sv)."
    if parts[0] == "vendor":
        return "vendor/ is upstream code (submodule or fetched): change it upstream, not here."
    if "target" in parts[:-1]:
        return "target/ is cargo build output: edit the crate's sources."
    if len(parts) == 1 and parts[0].startswith("firmware") and parts[0].endswith(".hex"):
        return "firmware*.hex at the root are symlinks into build/firmware/: rebuild with `make firmware`."
    return None


def main() -> None:
    data = read_input()
    rel = rel_path((data.get("tool_input") or {}).get("file_path"))
    reason = reason_for(rel) if rel else None
    if reason:
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
        print(json.dumps({"hookSpecificOutput": decision}))


if __name__ == "__main__":
    main()
