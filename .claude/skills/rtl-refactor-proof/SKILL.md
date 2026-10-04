---
name: rtl-refactor-proof
description: Prove a behavior-preserving VUX9K RTL refactor equivalent to its parent with make eqy (unified_cpu and soc_top), and read eqy's failures. Use when committing an RTL refactor, or when eqy fails or won't partition.
---

# Proving an RTL refactor (make eqy)

Rules and background: docs/agents/rtl-workflow.md, "Step 3 commit rule" (read it first).

1. The commit must be a refactor only: no cycle-visible change, no `soc_ram` port change
   (stubbed on both sides: such changes are their own, tested commit), nothing in `soc_ram`.
2. Commit it, then run both tops against the parent (remote CI; ~6 min each on 4 cores):
   `make eqy EQY_BASE=<parent-sha> EQY_TOP=unified_cpu` and `... EQY_TOP=soc_top`.
3. Also run the quick checks that eqy can't replace: `make check-rtl-syntax lint-rtl`
   (eqy reads both sides with Yosys, so Yosys-vs-simulator differences stay invisible:
   docs/agents/veryl.md, "Veryl constructs the toolchain rejects").

Reading failures (details under `build/eqy/<top>/<top>/`):
- "conflicting matches ... Failed to partition design": too much renamed or restructured at
  once. Split the refactor into smaller commits, or add `[match]` hints; never skip the check.
  `EQY_NOMATCH="<patterns>"` only removes cut points (still sound).
- "Failed to prove equivalence of partition X": first raise the induction depth (default 5;
  `make eqy` doesn't pass it: `python3 scripts/run_eqy.py --base <sha> --top <top> --depth 10`);
  if it still fails, the change is unreachable only because of how another block drives it
  (proofs are per partition), or it is a real behavior change. Either way: treat it as a
  behavior change and test it, in its own commit.
