---
paths:
  - "scripts/mcy/**"
  - "coverage/**"
  - "scripts/coverage_fw.py"
  - "scripts/coverage_rust.py"
---

# Mutation testing and code coverage

Read before running or changing `make mutation`, `make coverage` or their thresholds. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Mutation testing: `make mutation` (mcy, manual only)

`make mutation MCY_TOP=<module>|all [MCY_SIZE=100]` (`scripts/mcy/mutation.py`) asks
whether the unit tests notice a broken RTL module: mcy lists single-point mutations of
the module's generated `.sv` (flattened with its submodules), Yosys `equiv_*` drops the
equivalent ones, and the module's cocotb unit tests (plus the riscv-tests for
`unified_cpu`) run against each remaining mutant, swapped in by `sim_runner`'s
`VUX9K_RTL_OVERRIDE`. Results: `build/mcy/<module>/summary.md` (score and every survivor's
source location), each mutant's test output in `logs/<id>.out`. It is in no tier and has
no threshold: read the survivors, then add a test or note why the mutant is harmless.
Things that went wrong while building it, and must stay fixed:
- **A broken harness looks like a perfect score.** Every mutant "fails" if the tests
  can't run at all. Two such cases: Yosys `write_verilog` renders `$pmux` as functions
  Icarus can't compile (the mutant is now written after `pmuxtree`, and a mutant that
  doesn't compile stops the run), and mcy runs its test scripts under OSS CAD Suite's
  Python wrapper, whose `PYTHONHOME` broke the venv's Python (the script unsets it). The
  run therefore starts with a self-test: the unmutated module through the same script,
  under the same wrapper, must pass (`logs/selftest.out`).
- **The equivalence check needs `memory_map` and `async2sync`**; without them every
  sequential mutant, even mcy's no-op mutation 1, counted as non-equivalent.
- `unified_cpu`'s mutants run one at a time (`run_riscv_tests.py` regenerates shared
  files); expect hours for `MCY_SIZE=100`.

Reading the survivors (2026-10: at size 50, 20 for `unified_cpu`, none is open in any module):
- **Equivalence is decided twice.** `equiv_induct` starts from any state, so a mutant that
  differs only in states the module can't reach (sdcard_spi's `bit_idx` counting
  0,1,6,3,4,5,2,7 is still 8 steps) fails it. For modules with `clk` and `rst` a proof
  from reset follows: the miter with `rst` asserted in step 0, built like sby's AIGER
  model, proven by abc `dprove` (`EQ_RESET`). It took sdcard_spi from 9 survivors to 1.
  Storage without a reset (a FIFO's memory) starts at zero on **each side before the
  miter**: `setundef -zero` on the miter also rewrote `-ignore_gold_x`'s x constants into
  a mask that hid real differences, and a detected mutant came out equivalent. The
  self-test therefore also requires the original to be equivalent to itself and one of
  the first mutants not to be. The proof uses the module's default parameters.
- **A mutated flip-flop output toggles.** `inv`/`cnot` on a `Q` port also feeds the hold
  path (`prep` leaves it as a mux from Q), so the register flips every cycle it holds.
  Whether that shows depends on the hold length's parity: such mutants are often
  equivalent (the proof above) or visible only outside the interface's contract.
- **`ACCEPTED`** in `mutation.py` lists survivors that are equivalent beyond `dprove`'s
  reach (uart_rx's 434-clock timer, the count enumerated instead; a Hack-only register in
  `unified_cpu`) or differ only where the interface promises nothing (uart_rx's `data`
  without `rdy`, fifo_sync's `rdata` while empty), each with its reason; `summary.md` shows them apart from the open
  ones and names entries no survivor matched any more. A survivor that is merely untested
  gets a test, not an entry (the `cov:exclude` rule). `unified_cpu`'s riscv-tests run in a
  256 KB window: what the upper address and PC bits do is the unit test's job
  (`test_unified_cpu.py`).
- `--replay <id>` reruns a module's tests on one mutant of the last run (`--node` picks
  the pytest node), keeping `mutated.v` and the output in `replay/<id>/`; `--summarize`
  rewrites the summaries after editing `ACCEPTED`. mcy's seed is fixed, so ids repeat
  as long as the RTL doesn't change; compare by `summary.md`'s description otherwise.

## Code coverage: `make coverage` (Verilator line + toggle)

`HDL_COVERAGE=1` makes `sim_runner.py` build Verilator RTL sims with
`--coverage-line --coverage-toggle` into their own `build/sim/verilator-cov/` tree
(so normal builds are untouched); each test writes `coverage.dat` into its run
directory. **Not `COVERAGE`:** cocotb 2.0 still reads that (deprecated) name as
"collect Python coverage of the testbench", which needs the `coverage` pip package —
not in CI's dependencies, so every test failed there with "coverage module not
available" while passing in a dev venv that happened to have it. `make coverage` runs every RTL unit + SoC test that way (incl. the slow
hw-flow), merges them with `verilator_coverage` into `build/coverage/merged.dat`
(+ `merged.info` for lcov) and annotates the generated `.sv` into
`build/coverage/annotated/`. When reading the annotation:
- Points are per hierarchy (unit-test toplevel vs `tb_soc_top.soc.cpu_inst`), so a
  `~` (partial) line is often covered by another instance; `%` lines were reached by
  nothing at all. `--annotate-points` shows every point with its `hier=`.
- The riscv-tests are measured too (`run_riscv_tests.py` under `HDL_COVERAGE=1`,
  run directories in `build/riscv_tests/runs-cov/`, hierarchy `tb_hex_runner.uut`).
  GLS is not.

## Rust coverage: `make coverage-rust` (cargo-llvm-cov, in test-sim)

Line coverage of the host Rust code, checked per crate against `coverage/thresholds.toml`'s
`[rust]` by `scripts/coverage_rust.py` (summary and per-file table in
`build/coverage/rust/summary.md`, lcov and JSON in `report/`). The emulator workspace
(`vux9k_emu`, `vux9k_emu_cli`, `vux9k_emu_py`) is measured from its `cargo test` **and**
from the Python-driven runs: `cargo llvm-cov show-env` supplies the instrumentation
environment (`RUSTC_WRAPPER`, `LLVM_PROFILE_FILE` with `%p-%4m`, so every process writes
its own profile), the release build's `.so` is copied to `build/coverage/rust/python/`, and
`VUX9K_EMU_PY_DIR`/`VUX9K_EMU_BIN` point `sim/emu` (with the lockstep trace), the
riscv-tests and ACT4 on the emu backend at that build and at the instrumented
`vux9k-emu`. `report` picks up the cdylib and the CLI as objects. `fw_common` is measured
from its host tests only (its RV32 side is `coverage-fw`'s). Both workspaces build into
their own target dirs under `build/coverage/rust/`, so the normal builds are untouched.
- `report` warns "23 functions have mismatched data" (2026-10, already from `cargo test`
  alone): a function's profile hash differs between the objects that contain it (test
  binaries, cdylib, CLI). It doesn't fail the run; treat a sudden change in that count as
  something to look at.
- Stable Rust has no exclusion comment (`#[coverage(off)]` is nightly-only), so unlike
  `coverage-fw` there is no `cov:exclude`: a minimum below 100 stands for code known not to
  run. Raise a minimum when coverage rises; never lower one.
- The `[rust]` minimums are floors measured on CI's toolchain; when a run reports more,
  raise them.
