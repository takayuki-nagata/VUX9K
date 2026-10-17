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
| `soc_pkg::CLK_HZ` | every copy listed there: emulator, `fw_common::map`, `fw_drivers`, the first stage's banner, Zephyr dts/Kconfig, test constants, and the PSRAM controller's cycle counts (`XMEM_*_LATENCY`, `PSRAM_INIT_CYCLES` and the window row below); `make sim-psram-contract` | clock-and-board.md, "The SoC clock" |
| `soc_pkg`'s wait-state window (`RV_REGION_XMEM`, `XMEM_WORDS`, `XMEM_READ_LATENCY`/`XMEM_WRITE_LATENCY`, `PSRAM_INIT_CYCLES`), the PSRAM controller's tag, `sim/psram_word_model.veryl`, the window's caches (`soc_icache`/`soc_dcache`: size, line keying, what a store does to a line, how long their clearing takes) | the emulator's copies (`XMEM_*` in `emu/crates/vux9k_emu/src/soc.rs`, the caches in `emu/crates/vux9k_emu/src/cache.rs`; the lockstep program `xcache`, `soc_env.CACHE_CLEAR_CYCLES`), the test constants (`lockstep_programs.XMEM`/`XMEM_BYTES`/`XMEM_WAIT` and the ranges `XMEM_USED` that `test_soc_lockstep.py` zeroes in the word model, `test_psram_contract.py`, `test_soc_rv32i.py`'s window checks), `soc_env.start_soc`'s skip of the word model's power-up (`init_count`), `fw_common::map::XMEM_*` and `hw_test`'s `xmem*` checksums (`xmem_time`'s packs the two latencies, as the caches' misses and the store, and their hits), README's address map; `make sim-lockstep` and `make sim-psram-contract` (the word model must match the controller); for the controller's tag or `soc_xmem_bridge`, also `make sim-psram-icarus` (test-slow: the window through the real controller and its netlist) | rtl-workflow.md, "The emulator must follow the RTL" |
| The first stage (`firmware/stage1`) | raise `STAGE1_VERSION` (tests and `make dist` read it from `main.rs`) | firmware.md, "Firmware rules" |
| The second stage (`firmware/stage2`) | raise `STAGE2_VERSION` (the Makefile's `mkv4 --version` and `sim/emu/s1_env.py` read it from `main.rs`) | firmware.md, "Firmware rules" |
| The second stage's commands or their output (`shell.rs`'s `HELP`/`CARD_CMDS`, `[ERR]`, `[PUT]`, `[RUN]`, `[INSTALL]`, the prompt) | `tools/vux_tool.py` (`s2_cmd`, `put`, `run_app`, `install_stage2`, `enter_s1`, `enter_s2`), `scripts/test_hardware.py` and `hw_irq_echo.py`, `sim/emu/test_stage2.py` and `s1_env.py`, `sim/integration/test_soc_hardware_flow.py`; `make sim-hw-suite-emu` (test-slow: the board suite on the emulator) | firmware.md, "Firmware rules" |
| The launcher's windows and targets (`fw_common::launch`, `map::ROM_*`/`XMEM_BASE`/`STAGE2_BASE`/`HACK_I_*`/`HACK_D_*`) | `put_run_error`'s text (a const assert), `tests/launch.rs`, `sim/emu/test_stage2.py`'s `run` tests, the launch programs in `lockstep_programs.py` (`s2_launch`), README's address map and v4 table, docs/APP_DEVELOPMENT.md's `mkv4` table, `vux_tool`'s default `load_addr` (`XMEM_BASE` for RV32; 0 for Hack, an offset in its instruction segment); a change breaks applications on cards (row "Anything an application or an SD card relies on") | firmware.md, "Firmware rules" |
| Hack's segments in the window (`soc_pkg::HACK_I_BASE`/`HACK_D_BASE`, how `soc_top` joins the fetch and data addresses from them) | the copies: the emulator (`HACK_I_BASE`/`HACK_D_BASE` in `emu/crates/vux9k_emu/src/soc.rs`), `fw_common::map::HACK_*` (the second stage's `start_hack`, `launch::staging`), `tools/vux_tool.py`, `lockstep_programs.py` (`HACK_*`, `hack_stub`, the segments' ranges in `XMEM_USED`), `sim/emu/test_stage2.py`; README's Hack address space and the launcher's table, docs/APP_DEVELOPMENT.md's Hack section (the 64 KB / 96 KB segments, whole-word data stores); `make sim-lockstep`; a change breaks Hack applications on cards | rtl-workflow.md, "The emulator must follow the RTL" |
| The second stage's window or the first stage's RAM (`fw_common::map::STAGE2_*`, `S1_RAM_*`) | both stages' `bootstrap/link.x` (they assert what they can), the Makefile's `STAGE2_BASE`, `boot::put_boot_error`'s out-of-window text (a const assert), `STAGE2_BASE` in `sim/emu/s1_env.py` and `sim/integration/soc_env.py`, `lockstep_programs.XMEM_USED`, README's address map; a move breaks cards (row "Anything an application or an SD card relies on") | firmware.md, "Firmware rules" |
| The VUX9 file header (v4) | `fw_common::header` **and** `ImageHeader`/constants in `tools/vux_tool.py`; regenerate `firmware/fw_common/tests/vux9_v4_vectors/` | firmware.md, "Firmware rules" |
| Transfer v2 (`fw_common::upload::upload_v2`) | `vux_tool.send_upload_v2` and the reference device in `sim/emu/test_upload_v2.py`; regenerate `firmware/fw_common/tests/upload_v2_vectors/` | firmware.md, "Firmware rules" |
| `fw_common::fat`'s image fixtures (`sim/emu/test_fat_images.py`'s `IMAGES`), `vux_tool.build_fat32_image` | regenerate `firmware/fw_common/tests/fat_images/` (`VUX9_VECTORS_UPDATE=1`, needs dosfstools/mtools) and the expectations in `tests/fat.rs`; `make test-fat` | firmware.md, "Firmware rules" |
| Transfer v2, the first stage's RAM boot or the second stage's install, the SD boot chain (`firmware/stage1`, `fw_common::fat`, `fw_drivers::sdcard`), `sim/integration/` UART/SD models | run `sim-gls-hw-flow` by hand (not in `test-sim`) | test-tiers.md |
| The SD card model | Python (`sim/integration/sdcard_model.py`) **and** Rust (emulator); `sim/sd_transcripts/` green on both | rtl-workflow.md, "The emulator must follow the RTL" |
| Anything an application or an SD card relies on (RV32/Hack address spaces and registers, Hack's segments, CSRs, the v4 header, `\VUX9K\STAGE2.BIN` and its load window, the first stage's a0-a2, start-up state, the soft reset back to the first stage) | README's tables and docs/APP_DEVELOPMENT.md; a change that breaks applications or cards says so in its commit message, so the next release raises minor/major and its tag message's `compatibility` field tells | dist-release.md, "Distribution and releases: `make dist`, tags vouch for a board test" |
| A CSR (new access in firmware, or the CPU's set) | `csr_exists` in `rv32i_trap_unit` **and** `rv32i_csrs`; README "RV32 CSRs" | isa-tests.md, "`make test-isa`" |
| A failing/fixed ISA test | `EXPECTED_FAILURES` / `EXPECTED_FAILURES_ACT4` (an XPASS fails the run) | isa-tests.md |
| `tohost` address | `scripts/riscv_tests/link.ld` **and** the tb's `TOHOST_ADDR` | isa-tests.md, "`make test-isa`" |
| ACT4 configuration | `vux9k.yaml`, `sail.json`, `rvmodel_macros.h`/`link.ld` together | isa-tests.md, "`make test-act4`" |
| A cocotb test touching a new internal signal | add it to `SOC_RTL_PUBLIC` in `sim/runners/sim_runner.py` (Verilator) | sim.md, "Verilator" |
| A generated `.sv` path (moving a `.veryl` file) | `Makefile` source lists, `sim/runners/sim_runner.py`, `scripts/run_riscv_tests.py` | layout.md, "Veryl module resolution is directory-agnostic" |
| Distribution contents | `make_dist.py` `FILES`, `check_dist.py` `REQUIRED` and `CARD_FILES`, docs/APP_DEVELOPMENT.md (its Python examples are `check_dist.PY_EXAMPLE` and `PY_CARD_EXAMPLE`), docs/dist-README.md's table; the Makefile's `apps` for `sd/VUX9K/APPS/`; `PINS` when ci.yml changes shape | dist-release.md |
| Firmware size | `make firmware-size` (the first stage: 16 KB, about 2 KB left; the second stage's size is printed) | firmware.md, "Cargo workspace gotcha" |
| `hw_test`'s tests | new checksums from the emulator (`sim/emu/test_hw_test.py` prints them) | clock-and-board.md, "The SoC clock" |
| Coverage | raise a threshold when coverage rises, never lower one; `cov:exclude(reason)` only for lines that can't run by design (firmware; host Rust has none) | firmware.md, "Firmware coverage"; sim.md, "Unit tests"; quality.md, "Rust coverage: `make coverage-rust` (cargo-llvm-cov, in test-sim)" |
| A mutation survivor | a test, or an `ACCEPTED` entry with its reason in `scripts/mcy/mutation.py` | quality.md, "Mutation testing" |
| Synthesis flags | re-measure with `make timing` | rtl-workflow.md, "Synthesis flags" |
| `pyproject.toml`'s ruff/mypy scope, a crate, a Makefile target the hooks run, where generated or upstream code lives, a tool pin | `.claude/hooks/` and the guides' `paths:` per its table | agent-tooling.md |
| A file named in these docs, or a section title cited from code (`docs/agents/x.md, "Title"`) | the docs and the citations; `make check` runs `scripts/check_agent_docs.py` | AGENTS.md |
