#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Formal equivalence check of the working tree's RTL against a base commit (make eqy).

For behavior-preserving refactors: proves with Yosys eqy that <top> as generated
from the working tree (gate: build/veryl) is equivalent to <top> generated from
--base (gold). The base is exported with `git archive` into a temporary directory
*outside* the repository (Veryl scans the whole project root, so a second copy of
the sources inside it would clash) and built there with the same `veryl`.

Both sides are flattened below <top>, so a refactor may change submodule
interfaces or move logic between submodules; only <top>'s own ports must match.

soc_ram is always replaced by a ports-only black box on both sides: its
$readmemh() initial block needs firmware.hex, and its RAM arrays are too large
to prove anything about usefully. Changes inside soc_ram are therefore not
checked; its ports must match.

Everything lands in build/eqy/: gold-<sha>/ (cached per base commit) and
<top>/ (snapshot of both sides, the .eqy file and eqy's work directory).
Exit code: 0 when equivalent, non-zero otherwise.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EQY_DIR = REPO_ROOT / "build" / "eqy"
PACKAGE = Path("soc/cpu/rv32i_pkg.sv")  # always present; every *_pkg.sv is read before the modules
BLACKBOX = {"soc_ram": Path("soc/soc_ram.sv")}


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def build_gold(base: str, veryl: str) -> Path:
    """Generated .sv tree of `base`, built once per commit into build/eqy/gold-<sha>/."""
    sha = run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"], cwd=REPO_ROOT, capture_output=True, text=True)
    sha = sha.stdout.strip()
    gold = EQY_DIR / f"gold-{sha[:12]}"
    if (gold / ".done").exists():
        return gold
    shutil.rmtree(gold, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="vux9k-eqy-") as tmp:
        archive = run(["git", "archive", sha], cwd=REPO_ROOT, capture_output=True).stdout
        run(["tar", "-x", "-C", tmp], input=archive)
        print(f"=== eqy: building gold RTL of {base} ({sha[:12]}) with {veryl} ===")
        run([veryl, "build", "--out-dir", "build/veryl"], cwd=tmp, stdout=subprocess.DEVNULL)
        shutil.copytree(Path(tmp) / "build" / "veryl" / "soc", gold / "soc")
    (gold / ".done").touch()
    return gold


def blackbox_stub(src: Path) -> str:
    """Ports-only copy of a Veryl-generated module: its header up to the ');' closing the port list."""
    lines = src.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() == ");":
            return "\n".join(lines[: i + 1]) + "\nendmodule\n"
    raise SystemExit(f"run_eqy.py: no port list end (a ');' line) found in {src}")


def snapshot(src_root: Path, dst: Path) -> list[Path]:
    """Copy src_root/soc/**.sv to dst (black boxes stubbed); return the files in read order."""
    shutil.rmtree(dst, ignore_errors=True)
    files = sorted(p.relative_to(src_root) for p in (src_root / "soc").rglob("*.sv"))
    if PACKAGE not in files:
        raise SystemExit(f"run_eqy.py: {src_root / PACKAGE} missing")
    out = []
    packages = [f for f in files if f.name.endswith("_pkg.sv")]
    for rel in packages + [f for f in files if f not in packages]:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel in BLACKBOX.values():
            target.write_text(blackbox_stub(src_root / rel))
        else:
            shutil.copyfile(src_root / rel, target)
        out.append(target)
    return out


def eqy_config(top: str, gold: list[Path], gate: list[Path], depth: int, nomatch: list[str]) -> str:
    def side(name, files):
        blackboxes = " ".join(BLACKBOX)
        return (
            f"[{name}]\n"
            f"read_verilog -sv {' '.join(str(f) for f in files)}\n"
            f"blackbox {blackboxes}\n"
            f"hierarchy -top {top} -purge_lib\n"  # drop black boxes <top> doesn't instantiate
            # Flattened, so changes to submodule interfaces (a removed port, logic moved
            # between modules) are internal nets rather than mismatched module boundaries
            f"prep -flatten -top {top}\n"
            "memory_map\n"
        )

    # Names eqy must not use to pair up gold and gate nets, e.g. the ports of a newly
    # extracted instance, which alias existing nets and confuse the matching
    match = "\n[match *]\n" + "".join(f"gold-nomatch {p}\ngate-nomatch {p}\n" for p in nomatch) if nomatch else ""
    return (
        side("gold", gold)
        + "\n"
        + side("gate", gate)
        + match
        + f"\n[strategy sby]\nuse sby\ndepth {depth}\nengine smtbmc bitwuzla\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Check RTL equivalence of the working tree against a base commit.")
    parser.add_argument("--base", default="HEAD", help="Commit whose generated RTL is the reference (default: HEAD)")
    parser.add_argument("--top", default="unified_cpu", help="Module to compare (default: unified_cpu)")
    parser.add_argument("--depth", type=int, default=5, help="sby induction depth per partition (default: 5)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--veryl", default=os.environ.get("VERYL", "veryl"))
    parser.add_argument(
        "--nomatch",
        nargs="*",
        default=[],
        help="Net-name patterns not to match between gold and gate (e.g. 'wr_decode.*' for a new instance)",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with eqy's output

    gate_src = REPO_ROOT / "build" / "veryl"
    if not (gate_src / PACKAGE).exists():
        raise SystemExit("run_eqy.py: build/veryl is missing; run `make veryl` first")

    gold_src = build_gold(args.base, args.veryl)
    work = EQY_DIR / args.top
    work.mkdir(parents=True, exist_ok=True)
    gold = snapshot(gold_src, work / "gold")
    gate = snapshot(gate_src, work / "gate")
    config = work / f"{args.top}.eqy"
    config.write_text(eqy_config(args.top, gold, gate, args.depth, args.nomatch))

    print(f"=== eqy: {args.top} of the working tree vs {args.base} (log: {work / args.top / 'logfile.txt'}) ===")
    result = subprocess.run(["eqy", "-f", "-j", str(args.jobs), config.name], cwd=work)
    verdict = "EQUIVALENT" if result.returncode == 0 else "NOT PROVEN EQUIVALENT"
    print(f"=== eqy: {args.top} vs {args.base}: {verdict} ===")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
