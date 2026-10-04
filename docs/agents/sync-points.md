---
paths:
  - "soc/**"
  - "emu/**"
  - "firmware/**"
  - "sim/**"
  - "tools/**"
  - "scripts/**"
  - "zephyr_workspace/**"
---

# Sync points: what has to change together

Things that exist in more than one place with nothing to generate one from the other. Each row
is the short form of a rule in the topic file it names. When your change touches the left
column, the rest of its row belongs to the same piece of work. Reviews check this list.

| When you change | Also change / run | Details |
|---|---|---|
| RTL behavior (anything cycle-visible) | the emulator (`emu/`), a lockstep program that exercises it (`sim/integration/lockstep_programs.py`), unit tests + fcov bins; `make sim-lockstep` | rtl-workflow.md, "The emulator must follow the RTL" |
| An RTL refactor | nothing behavioral; prove it with `make eqy EQY_TOP=unified_cpu` and `EQY_TOP=soc_top`; never in the same commit as a behavior change | rtl-workflow.md, "Step 3 commit rule" |
| `soc_pkg::CLK_HZ` | every copy listed there: emulator, `fw_common::map`, Boot Manager, Resident Loader, Zephyr dts/Kconfig, test constants | clock-and-board.md, "The SoC clock" |
| The Boot Manager | raise `BOOT_MGR_VERSION` (tests read it from `main.rs`) | firmware.md, "Firmware rules" |
| The VUX9 slot header | `fw_common::header` **and** `SlotHeader`/constants in `tools/vux_tool.py`; regenerate `firmware/fw_common/tests/vux9_vectors/` | firmware.md, "Firmware rules" |
| The UART flashing protocol, `build_vux9_image()`, the SD boot chain, `sim/integration/` UART/SD models | run `sim-gls-hw-flow` by hand (not in `test-sim`) | test-tiers.md |
| The SD card model | Python (`sim/integration/sdcard_model.py`) **and** Rust (emulator); `sim/sd_transcripts/` green on both | rtl-workflow.md, "The emulator must follow the RTL" |
| A CSR (new access in firmware, or the CPU's set) | `csr_exists` in `rv32i_trap_unit` **and** `rv32i_csrs`; README "RV32 CSRs" | isa-tests.md, "`make test-isa`" |
| A failing/fixed ISA test | `EXPECTED_FAILURES` / `EXPECTED_FAILURES_ACT4` (an XPASS fails the run) | isa-tests.md |
| `tohost` address | `scripts/riscv_tests/link.ld` **and** the tb's `TOHOST_ADDR` | isa-tests.md, "`make test-isa`" |
| ACT4 configuration | `vux9k.yaml`, `sail.json`, `rvmodel_macros.h`/`link.ld` together | isa-tests.md, "`make test-act4`" |
| A cocotb test touching a new internal signal | add it to `SOC_RTL_PUBLIC` in `sim/runners/sim_runner.py` (Verilator) | sim.md, "Verilator" |
| A generated `.sv` path (moving a `.veryl` file) | `Makefile` source lists, `sim/runners/sim_runner.py`, `scripts/run_riscv_tests.py` | layout.md, "Veryl module resolution is directory-agnostic" |
| Distribution contents | `make_dist.py` `FILES`, `check_dist.py` `REQUIRED`, docs/APP_DEVELOPMENT.md (its Python example is `check_dist.PY_EXAMPLE`); `PINS` when ci.yml changes shape | dist-release.md |
| Firmware size | `make firmware-size` (Boot Manager 14 KB, Resident Loader 2 KB with ~100 B left) | firmware.md, "Cargo workspace gotcha" |
| `hw_test`'s tests | new checksums from the emulator (`sim/emu/test_hw_test.py` prints them) | clock-and-board.md, "The SoC clock" |
| Coverage | raise a threshold when coverage rises, never lower one; `cov:exclude(reason)` only for lines that can't run by design (firmware; host Rust has none) | firmware.md, "Firmware coverage"; sim.md, "Unit tests"; quality.md, "Rust coverage: `make coverage-rust` (cargo-llvm-cov, in test-sim)" |
| A mutation survivor | a test, or an `ACCEPTED` entry with its reason in `scripts/mcy/mutation.py` | quality.md, "Mutation testing" |
| Synthesis flags | re-measure with `make timing` | rtl-workflow.md, "Synthesis flags" |
| `pyproject.toml`'s ruff/mypy scope, a crate, a Makefile target the hooks run, where generated or upstream code lives, a tool pin | `.claude/hooks/` and the guides' `paths:` per its table | agent-tooling.md |
| A file named in these docs, or a section title cited from code (`docs/agents/x.md, "Title"`) | the docs and the citations; `make check` runs `scripts/check_agent_docs.py` | AGENTS.md |
