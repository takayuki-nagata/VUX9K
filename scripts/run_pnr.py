#!/usr/bin/env python3
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""
nextpnr Place & Route Runner with Timing Closure Exploration.

Runs nextpnr once per candidate seed, in parallel, each into its own directory
(<seed-dir>/seed_<N>/{soc_pnr.json,soc_sta.json,nextpnr.log}). Seeds are judged
as they finish:
  - one meets timing        -> the others are stopped and that seed is adopted;
  - one is worse than --abort-slack -> seed jitter can't close that gap, so the
    others are stopped too (disable with --abort-slack none).
--all-seeds turns both stops off, so every seed is routed (make timing: the
spread across seeds is the measurement).
Without a closing seed, the finished seed with the best worst slack is adopted.

The adopted seed's results are copied to --write/--report, and the seed itself
is recorded in --seed-info, so the routed design is always traceable to a seed.
Timing failure is not an error here; `report_sta.py --strict` decides that.
"""

import argparse
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time


def get_timing_info(report_path, target_freq_mhz=27.0):
    if not os.path.exists(report_path):
        return False, None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        critical_paths = data.get("critical_paths", [])
        if not critical_paths:
            return True, 0.0
        target_period_ns = 1000.0 / target_freq_mhz
        worst_slack = float("inf")
        for cp in critical_paths:
            total_delay = sum(elem.get("delay", 0.0) for elem in cp.get("path", []))
            slack = target_period_ns - total_delay
            if slack < worst_slack:
                worst_slack = slack
        is_closure = worst_slack >= 0.0
        return is_closure, worst_slack
    except Exception:
        return False, None


def parse_abort_slack(value):
    return None if value.lower() == "none" else float(value)


def main():
    parser = argparse.ArgumentParser(description="Run nextpnr over candidate seeds in parallel and adopt the best.")
    parser.add_argument("--device", required=True)
    parser.add_argument("--vopt", action="append", default=[])
    parser.add_argument("--json", required=True)
    parser.add_argument("--write", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--freq", type=float, default=30.0)
    parser.add_argument("--seeds", nargs="+", type=int, default=[100])
    parser.add_argument("--seed-dir", required=True, help="Per-seed output directories go here (seed_<N>/)")
    parser.add_argument("--seed-info", required=True, help="JSON file recording the adopted seed")
    parser.add_argument("--jobs", type=int, default=None, help="Seeds run concurrently (default: min(#seeds, #CPUs))")
    parser.add_argument(
        "--abort-slack",
        type=parse_abort_slack,
        default=-1.5,
        help="Stop all seeds once one finishes below this slack in ns (default: -1.5; 'none' runs every seed)",
    )
    parser.add_argument("--all-seeds", action="store_true", help="Route every seed: no early stop of any kind")
    args, unknown = parser.parse_known_args()
    if args.all_seeds:
        args.abort_slack = None
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # per-seed lines show up as seeds finish, even in CI logs
    # Turn SIGTERM into SystemExit so the `finally` below kills the nextpnr children too
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    nextpnr_bin = os.environ.get("NEXTPNR", "nextpnr-himbaechel")
    base_cmd = [nextpnr_bin, "--device", args.device]
    for v in args.vopt:
        base_cmd.extend(["--vopt", v])
    base_cmd.extend(["--json", args.json, "--detailed-timing-report", "--freq", str(args.freq), "--timing-allow-fail"])
    base_cmd.extend(unknown)

    seeds = list(dict.fromkeys(args.seeds))
    jobs = max(1, args.jobs or min(len(seeds), os.cpu_count() or 1))
    abort_str = "off" if args.abort_slack is None else f"{args.abort_slack:.2f} ns"
    stops = "none (all seeds)" if args.all_seeds else f"on closure, or below {abort_str}"
    print(f"=== nextpnr: seeds {seeds}, {jobs} in parallel, target {args.freq:.2f} MHz, stop {stops} ===")

    def seed_path(seed, name):
        return os.path.join(args.seed_dir, f"seed_{seed}", name)

    pending = list(seeds)
    running = {}  # seed -> (Popen, log file)
    results: dict[int, float | None] = {}  # seed -> slack (None if nextpnr failed or the report is unreadable)
    stop_reason = None

    def start(seed):
        d = os.path.dirname(seed_path(seed, ""))
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        cmd = base_cmd + [
            "--write",
            seed_path(seed, "soc_pnr.json"),
            "--report",
            seed_path(seed, "soc_sta.json"),
            "--seed",
            str(seed),
        ]
        log = open(seed_path(seed, "nextpnr.log"), "w")
        running[seed] = (subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log)

    try:
        while pending or running:
            while pending and len(running) < jobs and stop_reason is None:
                start(pending.pop(0))
            if not running:
                break
            time.sleep(1)
            for seed, (proc, log) in list(running.items()):
                if proc.poll() is None:
                    continue
                log.close()
                del running[seed]
                if proc.returncode != 0:
                    results[seed] = None
                    log_path = seed_path(seed, "nextpnr.log")
                    print(f"  seed {seed:>4}: nextpnr failed (exit {proc.returncode}), see {log_path}")
                    continue
                closure, slack = get_timing_info(seed_path(seed, "soc_sta.json"), target_freq_mhz=args.freq)
                results[seed] = slack
                if slack is None:
                    print(f"  seed {seed:>4}: unreadable timing report")
                    continue
                print(f"  seed {seed:>4}: slack {slack:+.2f} ns, Fmax {1000.0 / (1000.0 / args.freq - slack):.2f} MHz")
                if stop_reason is None and closure and not args.all_seeds:
                    stop_reason = f"seed {seed} met timing"
                elif stop_reason is None and args.abort_slack is not None and slack < args.abort_slack:
                    stop_reason = f"seed {seed} is below {args.abort_slack:.2f} ns; seed jitter can't close that gap"
            if stop_reason is not None and (running or pending):
                print(f"  stopping the remaining seeds: {stop_reason}")
                for proc, _log in running.values():
                    proc.terminate()
                for proc, log in running.values():
                    proc.wait()
                    log.close()
                running.clear()
                pending.clear()
    finally:
        for proc, log in running.values():
            proc.kill()
            log.close()

    finished = {s: v for s, v in results.items() if v is not None}
    if not finished:
        print("❌ nextpnr produced no usable result for any seed.")
        return 1
    # Best worst-slack; ties go to the seed listed first, so the choice is deterministic
    best = max(finished, key=lambda s: (finished[s], -seeds.index(s)))
    slack = finished[best]
    closure = slack >= 0.0
    shutil.copyfile(seed_path(best, "soc_pnr.json"), args.write)
    shutil.copyfile(seed_path(best, "soc_sta.json"), args.report)
    with open(args.seed_info, "w", encoding="utf-8") as f:
        json.dump({"seed": best, "slack_ns": round(slack, 3), "closure": closure, "target_mhz": args.freq}, f)
        f.write("\n")

    if closure:
        print(f"\n✅ [TIMING CLOSURE] Adopted seed={best} (Slack: {slack:+.2f} ns)\n")
    else:
        print(f"\n❌ [WARNING] No seed met timing; adopted the best finished seed={best} (Slack: {slack:+.2f} ns)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
