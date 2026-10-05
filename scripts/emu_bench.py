#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""Speed of the emulator driven from Python, and a check that a change kept its behavior.

`make emu-bench` (or this script directly) runs each workload on the `vux9k_emu` module
three ways:
- `run_until_tx`: until the workload's UART token, as the tests drive it (the headline
  MIPS: instructions per wall-clock second, the best of `--repeat` runs);
- `run`: the same number of cycles without UART watching;
- with `--trace` (small workloads only): `step()` per instruction, hashing every result.
and prints MIPS plus a digest of the final state (cycle, instruction count, registers,
CSRs, every UART byte with its cycle, the memories). The three ways must end in the same
state.

`--json FILE` saves the results; `--compare FILE` checks digests against a saved run, e.g.
one made with VUX9K_EMU_PY_DIR pointing at the module built from the parent commit: a
speed-only change of the emulator must not change any digest. The numbers are only
comparable on one machine; CI prints them but never fails on speed.

Workloads whose image is missing are skipped (`--build-dir` points at another build/ tree,
e.g. one fetched from CI). `synthetic` needs no build: a fixed RV32I loop assembled here.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "emu"))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "integration"))

from rv32_asm import Asm  # noqa: E402
from vux9k import Soc, mbr_sector  # noqa: E402

# Memory sizes per profile (bytes), for the digest
SIZES = {"real": (16 * 1024, 8 * 1024), "extended": (512 * 1024, 256 * 1024)}


@dataclass
class Workload:
    name: str
    profile: str
    # Sets a fresh Soc up; returns False when the image is missing
    setup: Callable[[Any, str], bool]
    token: bytes
    budget: int
    # Small enough for a per-instruction step() trace
    traceable: bool


def synthetic_words(iterations: int = 2_000_000) -> list[int]:
    """ALU, D-RAM loads/stores and branches with interrupts enabled (MTIE; mtimecmp stays
    at its reset maximum, so none is taken), a '.' every 1024 iterations, then DONE."""
    a = Asm()
    a.li("s0", 0x4000_0000)  # UART
    a.li("s1", 0x2000_0000)  # D-RAM
    a.li("s2", iterations)
    a.li("s3", 0)
    a.li("a0", 12345)
    a.li("t0", 0x80)
    a.csrw("mie", "t0")
    a.li("t0", 0x8)
    a.csrs("mstatus", "t0")
    a.label("loop")
    a.addi("s3", "s3", 1)
    a.add("a0", "a0", "s3")
    a.slli("t1", "a0", 3)
    a.andi("t1", "t1", 0x7C)
    a.add("t2", "s1", "t1")
    a.lw("t3", 0, "t2")
    a.add("t3", "t3", "a0")
    a.sw("t3", 0, "t2")
    a.lbu("t4", 1, "t2")
    a.sub("a0", "a0", "t4")
    a.andi("t5", "s3", 0x3FF)
    a.bnez("t5", "skip")
    a.li("a1", ord("."))
    a.call("putc")
    a.label("skip")
    a.bne("s3", "s2", "loop")
    for ch in b"DONE\n":
        a.li("a1", ch)
        a.call("putc")
    a.label("halt")
    a.j("halt")
    a.label("putc")
    a.lw("t6", 4, "s0")
    a.andi("t6", "t6", 2)
    a.bnez("t6", "putc")
    a.sb("a1", 0, "s0")
    a.ret()
    return a.assemble()


def _read(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _preload(soc: Any, build: str) -> bool:
    texts = []
    for name in ["firmware.hex"] + [f"firmware_d{i}.hex" for i in range(4)]:
        data = _read(os.path.join(build, "firmware", name))
        if data is None:
            return False
        texts.append(data.decode())
    soc.load_readmemh(*texts)
    return True


def setup_synthetic(iterations: int) -> Callable[[Any, str], bool]:
    def setup(soc: Any, build: str) -> bool:
        soc.load_app(b"".join(w.to_bytes(4, "little") for w in synthetic_words(iterations)), "riscv")
        return True

    return setup


def setup_bc(soc: Any, build: str) -> bool:
    data = _read(os.path.join(build, "zephyr", "zephyr", "zephyr.bin"))
    if data is None:
        return False
    soc.load_iram(0, data)
    return True


def setup_demo(soc: Any, build: str) -> bool:
    data = _read(os.path.join(build, "zephyr-demo", "zephyr", "zephyr.bin"))
    if data is None:
        return False
    soc.load_app(data, "riscv")
    return True


def setup_hack(soc: Any, build: str) -> bool:
    data = _read(os.path.join(build, "hack", "firmware.hex"))
    if data is None:
        return False
    soc.load_iram_words([int(line, 16) for line in data.decode().split() if line])
    return True


def setup_bm(soc: Any, build: str) -> bool:
    if not _preload(soc, build):
        return False
    soc.sd_insert(mbr_sector())
    return True


WORKLOADS = [
    Workload("synthetic", "real", setup_synthetic(2_000_000), b"DONE\n", 400_000_000, False),
    Workload("synthetic-small", "real", setup_synthetic(20_000), b"DONE\n", 10_000_000, True),
    Workload("bc", "extended", setup_bc, b"bc> ", 2_000_000_000, False),
    Workload("demo", "real", setup_demo, b"All Rust application tasks finished successfully!", 40_000_000, True),
    Workload("hack", "real", setup_hack, b"ALL HACK C FIRMWARE TESTS PASSED (100%)!", 20_000_000, True),
    Workload("bm", "real", setup_bm, b"vux> ", 3_000_000, True),
]


def digest(soc: Any, profile: str) -> str:
    iram, dram = SIZES[profile]
    h = hashlib.sha256()
    state = (
        soc.cycle,
        soc.steps,
        soc.pc,
        soc.regs,
        soc.riscv_mode,
        soc.mcause,
        soc.mepc,
        soc.mtval,
        soc.mstatus,
        soc.leds,
        soc.uart_tx_log(),
    )
    h.update(repr(state).encode())
    h.update(b"".join(soc.iram_word(a).to_bytes(4, "little") for a in range(0, iram, 4)))
    h.update(b"".join(soc.dram_word(i).to_bytes(4, "little") for i in range(dram // 4)))
    return h.hexdigest()


def run_workload(w: Workload, build: str, trace: bool, repeat: int) -> dict[str, Any] | None:
    wall = float("inf")
    for _ in range(repeat):
        soc = Soc(w.profile)
        if not w.setup(soc, build):
            return None
        t0 = time.perf_counter()
        end = soc.run_until_tx(w.token, w.budget)
        wall = min(wall, time.perf_counter() - t0)
        if end is None:
            raise SystemExit(f"emu_bench: {w.name}: {w.token!r} not received within {w.budget} cycles")
    cycles, steps = soc.cycle, soc.steps
    result: dict[str, Any] = {
        "cycles": cycles,
        "steps": steps,
        "wall_s": round(wall, 3),
        "mips": round(steps / wall / 1e6, 2),
        "digest": digest(soc, w.profile),
    }

    wall = float("inf")
    for _ in range(repeat):
        soc = Soc(w.profile)
        w.setup(soc, build)
        t0 = time.perf_counter()
        soc.run(cycles)
        wall = min(wall, time.perf_counter() - t0)
    result["run_mips"] = round(soc.steps / wall / 1e6, 2)
    if digest(soc, w.profile) != result["digest"]:
        raise SystemExit(f"emu_bench: {w.name}: run() ends in another state than run_until_tx()")

    if trace and w.traceable:
        soc = Soc(w.profile)
        w.setup(soc, build)
        h = hashlib.sha256()
        while soc.cycle < cycles:
            h.update(repr(sorted(soc.step().items())).encode())
        result["trace"] = h.hexdigest()
        if digest(soc, w.profile) != result["digest"]:
            raise SystemExit(f"emu_bench: {w.name}: step() ends in another state than run_until_tx()")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workloads", default=",".join(w.name for w in WORKLOADS), help="comma-separated names")
    p.add_argument("--build-dir", default=os.path.join(REPO_ROOT, "build"), help="where the images are")
    p.add_argument("--trace", action="store_true", help="also hash a step() trace of the small workloads")
    p.add_argument("--repeat", type=int, default=3, help="time each way this many times, keep the best")
    p.add_argument("--json", help="save the results here")
    p.add_argument("--compare", help="fail unless digests (and traces) match this saved run")
    args = p.parse_args()

    names = args.workloads.split(",")
    unknown = set(names) - {w.name for w in WORKLOADS}
    if unknown:
        p.error(f"unknown workloads: {', '.join(sorted(unknown))}")
    results: dict[str, Any] = {}
    print(f"{'workload':<16} {'steps':>12} {'wall s':>8} {'MIPS':>7} {'run MIPS':>9}  digest")
    for w in WORKLOADS:
        if w.name not in names:
            continue
        r = run_workload(w, args.build_dir, args.trace, args.repeat)
        if r is None:
            print(f"{w.name:<16} skipped (image missing)")
            continue
        results[w.name] = r
        speed = f"{r['wall_s']:>8.2f} {r['mips']:>7.2f} {r['run_mips']:>9.2f}"
        print(f"{w.name:<16} {r['steps']:>12} {speed}  {r['digest'][:16]}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
    if args.compare:
        with open(args.compare) as f:
            base = json.load(f)
        bad = []
        for name, r in results.items():
            b = base.get(name)
            if b is None:
                print(f"compare: {name}: not in {args.compare}")
                continue
            for key in ("cycles", "steps", "digest", "trace"):
                if key in r and key in b and r[key] != b[key]:
                    bad.append(f"{name}.{key}")
        if bad:
            print(f"compare: MISMATCH {', '.join(bad)}")
            return 1
        print(f"compare: all digests match {args.compare}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
