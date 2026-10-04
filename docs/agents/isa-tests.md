---
paths:
  - "scripts/run_riscv_tests.py"
  - "scripts/riscv_tests/**"
  - "scripts/act4/**"
  - "sim/tb_hex_runner.veryl"
  - "soc/cpu/**"
---

# ISA tests (riscv-tests, ACT4) and the CSR rules

Read before changing the CPU's ISA behavior or CSRs, `tb_hex_runner`, or the ISA test harnesses. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## `make test-isa`: riscv-tests on `tb_hex_runner`

`scripts/run_riscv_tests.py` builds riscv-tests (`rv32ui`/`rv32mi`, `env/p`, both
pinned and fetched into `vendor/riscv-tests/`) and runs each on
`sim/tb_hex_runner.veryl`. It replaced a riscv-arch-test (ACT4) harness that could
never fail: its halt address (`0x1000`) sat *inside* the test code, so every test
"passed" after executing its first ~4 KB, and ACT4's self-checking needs expected
signatures from the Sail reference model, which were never generated. Things that
are easy to break without noticing:
- **Results travel through `tohost`, not the console.** `scripts/riscv_tests/link.ld`
  places `.tohost` at `0xF000_0000` (NOLOAD, outside the tb's 256 KB RAM window);
  the tb treats a store there as the verdict (`1` = PASS, `(TESTNUM<<1)|1` = FAIL).
  Keep the address in the linker script and the tb's `TOHOST_ADDR` in sync, and
  never let it alias into RAM.
- **Verdicts go through the CPU's trap path.** Upstream `env/p` reports PASS/FAIL
  via `ecall` -> `trap_vector` -> `write_tohost`, so a broken ECALL/MRET shows up
  as a harness self-test failure (typically a timeout), not as quietly green
  tests. From `02a105a` until the trap path was restored, a local
  `riscv_test.h` override sidestepped this; don't bring one back to paper over
  a trap regression.
- **`EXPECTED_FAILURES` is strict.** Known gaps are listed with reasons
  (`rv32ui-p-ma_data`: misaligned accesses trap rather than being done in
  hardware, and `env/p` has no handler to emulate them; `rv32mi-p-pmpaddr`: no
  PMP); a listed test that
  starts passing is reported as XPASS and fails the run — remove the entry
  when fixing the CPU rather than loosening the check.
- **The harness self-test must stay first.** `scripts/riscv_tests/selftest_fail.S`
  deliberately fails test case 2, and the run aborts unless it's reported as
  exactly `FAIL (TESTNUM=2)`. This is what proves the verdict plumbing works;
  don't remove it to "speed things up".
- **The CPU is M-mode only, with WARL CSRs the `rv32mi` tests depend on.**
  `mstatus` keeps only MIE/MPIE and reads MPP as M (`illegal`/`scall` write MPP
  and read it back to detect S/U-mode; a fully writable `mstatus` makes them take
  the S-mode path and fail), `mtvec` is direct-mode only, `misa` is read-only
  (so `ma_fetch` skips its RVC part). The Zicntr counters exist: `mcycle`/`minstret`
  (writable) with read-only `cycle`/`instret`, and `time` reads the timer's `mtime`;
  `minstret` counts RV32 instructions that complete without trapping (loads/stores
  in MEM_WAIT, MRET included), and a write to either half of a counter replaces
  that instruction's increment (`rv32mi-p-instret_overflow` checks this; it passed
  vacuously while the counters read 0). CSR writes happen only for CSRRW[I] or a
  non-zero rs1/uimm (`csr_wr`), so plain `csrr` reads have no side effects.
  **Accessing a CSR that isn't implemented is illegal** (as is writing a read-only
  one, `addr[11:10] == 3`, e.g. `unimp`). The legal set is `csr_exists` in
  `rv32i_trap_unit` (README, "RV32 CSRs"); it covers everything Zephyr and the
  riscv-tests env touch, checked by disassembling them. A few are legal only to
  read 0: `mhartid` and the other machine information registers, `mstatush`, and
  the debug trigger registers (`rv32mi-p-breakpoint` writes `tselect` without a
  trap guard), and `mcountinhibit` plus the HPM CSRs the spec requires
  (`mhpmevent3`-`31`, `mhpmcounter3`-`31`(h)), which the ACT4 tests' boot code writes
  unguarded. Before adding a CSR access to firmware, add the CSR to both
  `csr_exists` and `rv32i_csrs`.
- **The tb is split: hardware in Veryl, stimulus in cocotb.** `sim/tb_hex_runner.veryl`
  holds the RAM (preloaded from `program.hex` in the cwd), the CPU and the `tohost`
  latch; `scripts/riscv_tests/hex_runner.py` drives clock/reset, waits for the latch
  with one trigger (not per cycle) and writes `verdict.txt`. `run_riscv_tests.py
  rv32ui-p-add ...` runs a subset; set `TRACE=1` in the environment for a
  per-cycle PC/instruction log in `build/riscv_tests/runs/<test>/sim.log`. Tests run
  in parallel (`--jobs`, default: CPUs); each has its own run directory.
- **The tb's RAM has `soc_ram`'s timing: keep it.** Both ports read one cycle after
  the CPU presents the address, before that edge's write, and stores use `data_waddr`.
  Until 2026-10 it read combinationally at `pc_out`, which hid any fetch-address bug:
  with `pc_out` mutated to `pc_reg` (the address one cycle late) all 56 tests still
  passed; with the synchronous RAM the self-test fails. The combinational read was
  also why the suite took ~97 s on Icarus (each RAM write re-evaluated the reads
  against the whole array); it now takes ~20 s on 2 CPUs. On Icarus a test costs
  ~0.45 s against ~0.7 s on Verilator (process start-up dominates), so `test-isa`
  stays on Icarus, which also keeps unwritten RAM as X.
- **`make test-isa-gls` runs the suite on the gate-level `unified_cpu` netlist**
  (`make synth-units`; `--gls`, results in `runs-gls/`), the tb's memory staying RTL.
  It is the ISA-wide check for constructs Yosys reads differently from the simulators
  (the `as i32` compare in veryl.md), which the RTL runs and `make eqy` can't see. On
  Verilator, ~70 s with the compile; in `test-sim`.

## `make test-act4`: riscv-arch-test (ACT4) on the same harness

riscv-arch-test 4.1.0's ACT4 tests are self-checking ELFs: the generator runs each test on
the Sail reference model configured like this CPU and builds the expected values in, so a
test can't pass vacuously (the old ACT harness could, see above). `make test-act4`
(`-gls`, `-emu`; all in `test-sim`) runs them through `run_riscv_tests.py --suite act4` on
`tb_hex_runner`, with the riscv-tests' `tohost` (1 = PASS, 3 = FAIL); failure details go
to the testbench's console word (`console.txt` in the run directory). Things to know:
- **The ELFs are generated, not committed.** `make act4-elfs` (`scripts/act4/act4_elfs.py`)
  runs `scripts/act4/generate.sh`, i.e. the upstream image `ghcr.io/riscv/act4:4.1.0`
  (Sail 0.13.1, GCC 16) under docker, podman or podman-remote (`ACT4_ENGINE`), into
  `build/act4/<key>/`, key = a hash of `generate.sh` and `scripts/act4/vux9k/`. A
  configuration change makes new ELFs; CI caches them under the same key. Where no
  container engine exists, `ACT4_ELF_CACHE=<dir>` with `<dir>/<key>/` generated
  elsewhere is copied instead. A run takes ~6-12 min on 2 CPUs.
- **The configuration is three files that must agree:** `vux9k.yaml` (UDB: extensions and
  parameters, what tests are selected and how they check), `sail.json` (the reference
  model: memory map, mtvec/mtval/misaligned behavior, extensions) and
  `rvmodel_macros.h`/`link.ld` (the harness). The CPU starts at 0, the tests at
  `TEST_BASE` 0x4000 (Sail keeps its device tree at 0x1000): `link.ld` puts a jump at 0.
  `sail.json` enables Zihpm although the UDB doesn't list it: with it off, Sail traps on
  `mhpmevent3`-`31`, which the spec requires and the CPU implements (read 0). Sail's CLINT
  must stay enabled (the generator requires it) even though the harness has no timer.
- **Generator errors are configuration errors.** The UDB is validated against 4.1.0's
  schema (parameters that exist in later versions are rejected; some become required
  when others are set, e.g. `MCOUNTENABLE_EN`), and `tests/env/check_defines.h` requires
  every interrupt macro even when the harness can't raise one (they are empty here).
- **`EXPECTED_FAILURES_ACT4`** works like `EXPECTED_FAILURES` (an XPASS fails the run);
  its entries are the places where this harness or CPU legally differs from the Sail
  configuration (no interrupt sources, Sail's CLINT setting MTIP, read-only-zero
  `mcountinhibit`). The self-test (`scripts/act4/selftest_fail.S`, linked like the tests)
  must report FAIL with TESTNUM=1 and its console line before the suite runs.
- `tb_hex_runner` drives the `time` CSR from a cycle counter (as `timer_core` counts); the
  Zicntr tests need it to advance. The emulator's isa-test profile has no console: its
  ACT4 failures carry no details.
