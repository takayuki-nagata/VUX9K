---
name: mutation-triage
description: Run make mutation (mcy) for a VUX9K RTL module and work through the surviving mutants - test gap, equivalent, or outside the interface contract. Use when asked to run mutation testing or to resolve mutation survivors.
---

# Mutation survivors

Background, harness pitfalls and how to read survivors: docs/agents/quality.md,
"Mutation testing" (read it first, including "Reading the survivors").

1. Run remotely, never locally: `make mutation MCY_TOP=<module> MCY_SIZE=50` (seed fixed: ids
   repeat while the RTL is unchanged). `unified_cpu` runs mutants serially (hours). If an
   earlier remote `--ref` run of another revision used the host copy, start with
   `rm -rf build/veryl`.
2. Check the self-test passed (`logs/selftest.out`); a broken harness looks like 100 %.
3. For each open survivor in `build/mcy/<module>/summary.md`:
   - Look at the mutated cell in the generated `.sv` and the mutant's test log
     (`logs/<id>.out`); `--replay <id>` keeps `mutated.v` and the output.
   - **Untested behavior**: add a test that fails on the mutant (confirm with `--replay`),
     with fcov bins if the module has a random test; run new random tests with a few seeds.
   - **Equivalent beyond dprove, or only outside the interface contract**: an `ACCEPTED`
     entry in `scripts/mcy/mutation.py` with the reason; never for a merely untested case.
   - An RTL bug: `xfail(strict=True)` test first, then fix (behavior change: emulator +
     lockstep too).
4. `--summarize` after editing `ACCEPTED`; rerun the module to confirm no open survivors.
