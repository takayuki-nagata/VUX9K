#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
Formal equivalence check of the working tree's RTL against a base commit (make eqy).

For behavior-preserving refactors: proves with Yosys eqy that <top> as generated
from the working tree (gate: build/veryl) is equivalent to <top> generated from
--base (gold). The base is exported with `git archive` into a temporary directory
*outside* the repository (Veryl scans the whole project root, so a second copy of
the sources inside it would clash) and built there with the same `veryl`. `git archive`
leaves submodules out, so the Veryl path dependencies of the base's Veryl.toml (the
PSRAM controller in vendor/) are exported from their submodule at the commit the base
records for them.

Both sides are flattened below <top>, so a refactor may change submodule
interfaces or move logic between submodules; only <top>'s own ports must match.

soc_rom is always replaced by a ports-only black box on both sides: its
$readmemh() initial block needs firmware.hex, and its 16 KB array is too large
to prove anything about usefully. Changes inside soc_rom are therefore not
checked; its ports must match.

--gate-tie NAME=VALUE (make eqy EQY_GATE_TIE="NAME=VALUE ...") is for a refactor
that adds a top input whose constant value reproduces the old behavior (e.g. a
ready input that is 1 everywhere the design is used so far). On the gate side
only, the port is removed and the net of that name is driven by the constant, so
both sides have the same ports. That proves the tied design equal to the old
one; what the design does with the input at other values is untested here and
belongs to the next, tested commit.

Everything lands in build/eqy/: gold-<sha>/ (cached per base commit) and
<top>/ (snapshot of both sides, the .eqy file and eqy's work directory).
Exit code: 0 when equivalent, non-zero otherwise.
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EQY_DIR = REPO_ROOT / "build" / "eqy"
PACKAGE = Path("soc/cpu/rv32i_pkg.sv")  # always present; every *_pkg.sv is read before the modules
BLACKBOX = {"soc_rom": Path("soc/soc_rom.sv")}
# Generated trees compared: the SoC and the Veryl dependencies it declares (Veryl also
# writes its standard library under dependencies/std, which the SoC doesn't use)
RTL_DIRS = ("soc",) + tuple(
    f"dependencies/{name}" for name in tomllib.loads((REPO_ROOT / "Veryl.toml").read_text()).get("dependencies", {})
)


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
        export_path_dependencies(sha, Path(tmp))
        print(f"=== eqy: building gold RTL of {base} ({sha[:12]}) with {veryl} ===")
        run([veryl, "build", "--out-dir", "build/veryl"], cwd=tmp, stdout=subprocess.DEVNULL)
        for name in RTL_DIRS:
            if (Path(tmp) / "build" / "veryl" / name).is_dir():  # a dependency the base may not have yet
                shutil.copytree(Path(tmp) / "build" / "veryl" / name, gold / name)
    (gold / ".done").touch()
    return gold


def export_path_dependencies(sha: str, tree: Path) -> None:
    """Fill in the submodules that tree/Veryl.toml names as path dependencies, at the
    commits `sha` records for them (git archive exports a submodule as an empty directory)."""
    manifest = tree / "Veryl.toml"
    deps = tomllib.loads(manifest.read_text()).get("dependencies", {}) if manifest.exists() else {}
    for dep in deps.values():
        if not isinstance(dep, dict) or "path" not in dep:
            continue
        path = dep["path"]
        entry = run(["git", "ls-tree", sha, path], cwd=REPO_ROOT, capture_output=True, text=True).stdout.split()
        if len(entry) < 3 or entry[1] != "commit":
            continue  # a plain directory of the base, already in its archive
        archive = run(["git", "-C", path, "archive", entry[2]], cwd=REPO_ROOT, capture_output=True).stdout
        (tree / path).mkdir(parents=True, exist_ok=True)
        run(["tar", "-x", "-C", str(tree / path)], input=archive)


def blackbox_stub(src: Path) -> str:
    """Ports-only copy of a Veryl-generated module: its header up to the ');' closing the port list."""
    lines = src.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() == ");":
            return "\n".join(lines[: i + 1]) + "\nendmodule\n"
    raise SystemExit(f"run_eqy.py: no port list end (a ');' line) found in {src}")


def snapshot(src_root: Path, dst: Path) -> list[Path]:
    """Copy the .sv files under src_root's RTL_DIRS to dst (black boxes stubbed); return them in read order."""
    shutil.rmtree(dst, ignore_errors=True)
    files = sorted(p.relative_to(src_root) for d in RTL_DIRS for p in (src_root / d).rglob("*.sv"))
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


def parse_ties(ties: list[str]) -> dict[str, str]:
    """NAME=VALUE pairs of --gate-tie; VALUE is a Verilog constant, 0/1 meaning 1'b0/1'b1."""
    out = {}
    for tie in ties:
        name, sep, value = tie.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not value:
            raise SystemExit(f"run_eqy.py: --gate-tie wants NAME=VALUE, got {tie!r}")
        if value in ("0", "1"):
            value = f"1'b{value}"
        if not re.fullmatch(r"[0-9]+'[bdh][0-9a-fA-F_]+", value):
            raise SystemExit(f"run_eqy.py: --gate-tie {name}: {value!r} is not a constant like 1'b1 or 4'h3")
        out[name] = value
    return out


def check_ties(top: str, files: list[Path], ties: dict[str, str]) -> None:
    """Each tied name must be an input of <top> in the gate's generated .sv."""
    header = re.compile(rf"^module {re.escape(top)}\b.*?^\);", re.MULTILINE | re.DOTALL)
    for f in files:
        match = header.search(f.read_text())
        if match:
            break
    else:
        raise SystemExit(f"run_eqy.py: module {top} not found in the gate RTL")
    for name in ties:
        port = re.search(rf"^\s*(input|output|inout)\b[^\n]*?\b{name}\s*,?\s*(//.*)?$", match.group(0), re.MULTILINE)
        if not port:
            raise SystemExit(f"run_eqy.py: --gate-tie {name}: {top} (gate) has no port {name}")
        if port.group(1) != "input":
            raise SystemExit(f"run_eqy.py: --gate-tie {name}: {top}'s port {name} is an {port.group(1)}, not an input")


def eqy_config(
    top: str, gold: list[Path], gate: list[Path], depth: int, nomatch: list[str], ties: dict[str, str]
) -> str:
    def side(name, files, ties):
        blackboxes = " ".join(BLACKBOX)
        # A tied input stops being a port: `delete -port` turns it into a plain wire
        # (its uses inside the flattened top stay connected to it) and `connect -set`
        # drives that wire with the constant. Both work on the current module, hence
        # the `cd`; the assert repeats check_ties() inside Yosys.
        tie = "".join(
            f"cd {top}\nselect -assert-count 1 i:{port}\ndelete -port {port}\nconnect -set {port} {value}\ncd ..\n"
            for port, value in ties.items()
        )
        return (
            f"[{name}]\n"
            f"read_verilog -sv {' '.join(str(f) for f in files)}\n"
            f"blackbox {blackboxes}\n"
            f"hierarchy -top {top} -purge_lib\n"  # drop black boxes <top> doesn't instantiate
            # Flattened, so changes to submodule interfaces (a removed port, logic moved
            # between modules) are internal nets rather than mismatched module boundaries
            f"prep -flatten -top {top}\n" + tie + "memory_map\n"
        )

    # Names eqy must not use to pair up gold and gate nets, e.g. the ports of a newly
    # extracted instance, which alias existing nets and confuse the matching
    match = "\n[match *]\n" + "".join(f"gold-nomatch {p}\ngate-nomatch {p}\n" for p in nomatch) if nomatch else ""
    # No `insbuf -chain` before partitioning: it chains a buffer between every alias
    # name of a net so each can be a cut point, but the chain follows the netlist's
    # connection order, which differs between gold and gate whenever a refactor adds
    # a consumer to a net -- so one design split names the other kept together
    # ("conflicting matches for gold bit ...", false mismatches). Without it, all
    # names of a net stay one net on both sides.
    options = "\n[options]\ninsbuf off\n"
    return (
        side("gold", gold, {})
        + "\n"
        + side("gate", gate, ties)
        + options
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
    parser.add_argument(
        "--gate-tie",
        nargs="*",
        default=[],
        metavar="NAME=VALUE",
        help="Gate-only top input to remove and drive with a constant (e.g. instr_ready=1), "
        "for a refactor that adds an input whose constant value keeps the old behavior",
    )
    args = parser.parse_args()
    ties = parse_ties(args.gate_tie)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with eqy's output

    gate_src = REPO_ROOT / "build" / "veryl"
    if not (gate_src / PACKAGE).exists():
        raise SystemExit("run_eqy.py: build/veryl is missing; run `make veryl` first")

    gold_src = build_gold(args.base, args.veryl)
    work = EQY_DIR / args.top
    work.mkdir(parents=True, exist_ok=True)
    gold = snapshot(gold_src, work / "gold")
    gate = snapshot(gate_src, work / "gate")
    check_ties(args.top, gate, ties)
    config = work / f"{args.top}.eqy"
    config.write_text(eqy_config(args.top, gold, gate, args.depth, args.nomatch, ties))

    if ties:
        print(f"=== eqy: gate inputs tied: {', '.join(f'{n}={v}' for n, v in ties.items())} ===")
    print(f"=== eqy: {args.top} of the working tree vs {args.base} (log: {work / args.top / 'logfile.txt'}) ===")
    result = subprocess.run(["eqy", "-f", "-j", str(args.jobs), config.name], cwd=work)
    verdict = "EQUIVALENT" if result.returncode == 0 else "NOT PROVEN EQUIVALENT"
    print(f"=== eqy: {args.top} vs {args.base}: {verdict} ===")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
