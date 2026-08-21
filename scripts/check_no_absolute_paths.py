#!/usr/bin/env python3
# Copyright 2026 Takayuki Nagata All Rights Reserved.
# Checks tracked and staged repository files to prevent hardcoded absolute paths.

import os
import re
import sys
import subprocess

FORBIDDEN_PATTERNS = [
    (re.compile(r'/(home|var/home|Users)/[a-zA-Z0-9_\-]+'), "User home directory path"),
    (re.compile(r'/(root|tmp)/[a-zA-Z0-9_\-]+'), "System root/tmp absolute path"),
    (re.compile(r'[A-Za-z]:[\\/][a-zA-Z0-9_\-]+'), "Windows absolute path"),
]

IGNORED_DIRS = {
    '.git', '.venv', '.build', 'target', 'vendor', 'build_arch_test', 
    '__pycache__', '.pytest_cache', 'dependencies'
}

IGNORED_EXTENSIONS = {
    '.bin', '.hex', '.vvp', '.cf', '.o', '.json', '.lock', '.fs', '.map'
}

def get_tracked_files(repo_root):
    try:
        res = subprocess.run(['git', 'ls-files'], cwd=repo_root, capture_output=True, text=True, check=True)
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception:
        # Fallback to filesystem walk
        tracked = []
        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1]
                if ext not in IGNORED_EXTENSIONS:
                    tracked.append(os.path.relpath(os.path.join(root, file), repo_root))
        return tracked

def check_file(repo_root, rel_path):
    ext = os.path.splitext(rel_path)[1]
    if ext in IGNORED_EXTENSIONS:
        return []

    full_path = os.path.join(repo_root, rel_path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return []

    errors = []
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_idx, line in enumerate(f, start=1):
                # Allow standard shebangs on the first line
                if line_idx == 1 and line.startswith("#!"):
                    continue

                for pattern, desc in FORBIDDEN_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        errors.append((line_idx, line.strip(), desc, match.group(0)))
    except Exception as e:
        print(f"Warning: Could not check {rel_path}: {e}", file=sys.stderr)

    return errors

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = get_tracked_files(repo_root)

    total_violations = 0
    for rel_path in sorted(files):
        # Skip files in ignored directories
        parts = rel_path.split(os.sep)
        if any(p in IGNORED_DIRS for p in parts):
            continue

        errors = check_file(repo_root, rel_path)
        if errors:
            print(f"\n❌ [ERROR] Absolute path violation detected in {rel_path}:")
            for line_num, line_text, desc, matched in errors:
                print(f"   Line {line_num}: {desc} ('{matched}')")
                print(f"   > {line_text}")
                total_violations += 1

    if total_violations > 0:
        print(f"\n[FAIL] Found {total_violations} forbidden absolute path violation(s). Please use dynamic or relative paths.")
        sys.exit(1)
    else:
        print("✅ [PASS] No hardcoded absolute paths found in repository.")
        sys.exit(0)

if __name__ == "__main__":
    main()
