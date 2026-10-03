#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Line coverage of the firmware and demos from emulator runs (make coverage-fw).

The emulator records each executed instruction as (PC, ISA, instruction word)
(sim/emu/vux9k.py, VUX9K_COV_DIR). A hit counts for an image only where the image
has that same word at that PC, so code loaded later over the same addresses (SD
slots over the Boot Manager) is not credited to it.

- RV32 images: ELF files with a DWARF line table (the firmware's `coverage` cargo
  profile: release code plus line tables; the Makefile first checks that its code is
  byte-identical to the release image). A source line is covered if any instruction
  the line table assigns to it ran. Only files under the given source prefixes count.
- Hack images: the assembly `hcc -S` produces for the binary, one instruction per
  non-label line; the instruction count must equal the binary's word count and every
  numeric A-instruction must equal the binary's word at its index.

Lines marked `cov:exclude(reason)` (code that can't run by design) are left out.
Writes an lcov file and a Markdown summary, and fails if an image is below its
minimum in coverage/thresholds.toml.
"""

import argparse
import bisect
import glob
import os
import sys
import tomllib

from elftools.elf.elffile import ELFFile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_hits(cov_dir):
    rv: set[tuple[int, int]] = set()
    hack: set[tuple[int, int]] = set()
    for path in glob.glob(os.path.join(cov_dir, "*.cov")):
        with open(path) as f:
            for line in f:
                k = int(line, 16)
                pc, isa, word = k >> 33, (k >> 32) & 1, k & 0xFFFFFFFF
                (rv if isa else hack).add((pc, word))
    return rv, hack


def elf_words(elf):
    """Executable segment words by address."""
    words = {}
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD" or not seg["p_flags"] & 1:
            continue
        data, base = seg.data(), seg["p_vaddr"]
        for off in range(0, len(data) - 3, 4):
            words[base + off] = int.from_bytes(data[off : off + 4], "little")
    return words


def line_ranges(elf, prefixes):
    """{(file, line): [(lo, hi), ...]} from the DWARF line tables, repo files only."""
    out: dict[tuple[str, int], list[tuple[int, int]]] = {}
    dwarf = elf.get_dwarf_info()
    for cu in dwarf.iter_CUs():
        lp = dwarf.line_program_for_CU(cu)
        if lp is None:
            continue
        top = cu.get_top_DIE()
        comp_dir = top.attributes.get("DW_AT_comp_dir")
        comp_dir = comp_dir.value.decode() if comp_dir else ""
        files, dirs = lp["file_entry"], lp["include_directory"]
        v5 = lp["version"] >= 5

        def path_of(idx, files=files, dirs=dirs, comp_dir=comp_dir, v5=v5):
            # DWARF 5: file and directory indices from 0, directory 0 = comp dir;
            # DWARF 4: from 1, directory 0 = comp dir
            fe = files[idx if v5 else idx - 1]
            d = fe.dir_index
            if v5:
                base = dirs[d].decode()
            else:
                base = dirs[d - 1].decode() if d > 0 else comp_dir
            return os.path.normpath(os.path.join(comp_dir, base, fe.name.decode()))

        prev = None
        for entry in lp.get_entries():
            st = entry.state
            if st is None:
                continue
            if prev is not None and st.address > prev[0] and prev[2] != 0:
                path = prev[1]
                rel = os.path.relpath(path, REPO_ROOT)
                if any(rel.startswith(p) for p in prefixes):
                    out.setdefault((rel, prev[2]), []).append((prev[0], st.address))
            prev = None if st.end_sequence else (st.address, path_of(st.file), st.line)
    return out


_sources: dict[str, list[str]] = {}


def excluded(path, line):
    """A source line marked `cov:exclude(reason)`: code that can't run by design."""
    if path not in _sources:
        try:
            with open(os.path.join(REPO_ROOT, path)) as f:
                _sources[path] = f.read().split("\n")
        except OSError:
            _sources[path] = []
    text = _sources[path]
    return 0 < line <= len(text) and "cov:exclude(" in text[line - 1]


def cover_elf(name, elf_path, prefixes, rv_hits):
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        words = elf_words(elf)
        ranges = line_ranges(elf, prefixes)
    ranges = {k: v for k, v in ranges.items() if not excluded(*k)}
    ran = sorted(pc for pc, w in rv_hits if words.get(pc) == w)
    lines: dict[tuple[str, int], bool] = {}
    for key, rs in ranges.items():
        hit = False
        for lo, hi in rs:
            i = bisect.bisect_left(ran, lo)
            if i < len(ran) and ran[i] < hi:
                hit = True
                break
        lines[key] = lines.get(key, False) or hit
    return lines


def cover_hack(asm_path, bin_path, hack_hits):
    with open(bin_path, "rb") as f:
        data = f.read()
    words = [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data) - 1, 2)]
    index_line: list[int] = []
    literals = {}  # instruction index -> value of a numeric A-instruction
    with open(asm_path) as f:
        for n, raw in enumerate(f, 1):
            s = raw.split("//")[0].strip()
            if not s or (s.startswith("(") and s.endswith(")")):
                continue
            if s.startswith("@") and s[1:].isdigit():
                literals[len(index_line)] = int(s[1:])
            index_line.append(n)
    if len(index_line) != len(words):
        sys.exit(f"{asm_path}: {len(index_line)} instructions, {bin_path} has {len(words)} words")
    # Alignment check: every @number must be the binary's word at the same index
    bad = [i for i, v in literals.items() if words[i] != v]
    if bad or not literals:
        sys.exit(f"{asm_path} does not match {bin_path} (A-instructions at {bad[:5]})")
    ran = {pc // 2 for pc, w in hack_hits if pc // 2 < len(words) and words[pc // 2] == w}
    rel = os.path.relpath(asm_path, REPO_ROOT)
    lines: dict[tuple[str, int], bool] = {}
    for i, n in enumerate(index_line):
        lines[(rel, n)] = lines.get((rel, n), False) or (i in ran)
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hits", required=True, help="directory of *.cov files")
    ap.add_argument("--elf", action="append", default=[], metavar="NAME=ELF=PREFIX[,PREFIX]")
    ap.add_argument("--hack", action="append", default=[], metavar="NAME=ASM=BIN")
    ap.add_argument("--lcov", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--thresholds", help="TOML: [line] name = minimum percent")
    args = ap.parse_args()

    rv_hits, hack_hits = load_hits(args.hits)
    if not rv_hits and not hack_hits:
        sys.exit(f"no coverage data in {args.hits}")
    results = {}
    for spec in args.elf:
        name, path, prefixes = spec.split("=", 2)
        results[name] = cover_elf(name, path, prefixes.split(","), rv_hits)
    for spec in args.hack:
        name, asm, binp = spec.split("=", 2)
        results[name] = cover_hack(asm, binp, hack_hits)

    os.makedirs(os.path.dirname(os.path.abspath(args.lcov)), exist_ok=True)
    with open(args.lcov, "w") as f:
        for name, lines in results.items():
            by_file: dict[str, dict[int, bool]] = {}
            for (path, n), hit in lines.items():
                by_file.setdefault(path, {})[n] = hit
            for path in sorted(by_file):
                f.write(f"TN:{name}\nSF:{os.path.join(REPO_ROOT, path)}\n")
                for n in sorted(by_file[path]):
                    f.write(f"DA:{n},{int(by_file[path][n])}\n")
                hits = sum(by_file[path].values())
                f.write(f"LH:{hits}\nLF:{len(by_file[path])}\nend_of_record\n")

    thresholds = {}
    if args.thresholds:
        with open(args.thresholds, "rb") as f:
            thresholds = tomllib.load(f).get("line", {})
    md = [
        "# Firmware line coverage (emulator runs)",
        "",
        "| image | file | lines | covered | % |",
        "|---|---|---|---|---|",
    ]
    failed = []
    for name, lines in results.items():
        counts: dict[str, list[int]] = {}
        for (path, _), hit in lines.items():
            t = counts.setdefault(path, [0, 0])
            t[0] += 1
            t[1] += hit
        total = sum(t[0] for t in counts.values())
        covered = sum(t[1] for t in counts.values())
        for path in sorted(counts):
            t = counts[path]
            md.append(f"| {name} | {path} | {t[0]} | {t[1]} | {100 * t[1] / max(t[0], 1):.1f} |")
        pct = 100 * covered / max(total, 1)
        md.append(f"| **{name}** | **total** | **{total}** | **{covered}** | **{pct:.1f}** |")
        want = thresholds.get(name)
        if want is not None and pct + 1e-9 < want:
            failed.append(f"{name}: {pct:.1f}% < {want}%")
        print(
            f"{name:16} {covered:5} / {total:5} lines  {pct:5.1f}%" + (f"  (min {want}%)" if want is not None else "")
        )
    with open(args.summary, "w") as f:
        f.write("\n".join(md) + "\n")
    if failed:
        sys.exit("coverage below the minimum: " + "; ".join(failed))


if __name__ == "__main__":
    main()
