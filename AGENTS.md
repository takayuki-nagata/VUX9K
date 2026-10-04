# AGENTS.md

Guidance for AI agents working in this repository. For user-facing documentation
(architecture, memory map, host tooling usage, build/flash instructions), see
[`README.md`](README.md). This file holds the rules that apply to every change and an index
of [`docs/agents/`](docs/agents/), one guide per area, each recording things that aren't
obvious from the source and caused real mistakes. Read the guides for the areas you touch
before changing them (Claude Code loads them by path through `.claude/rules/`), and
[`docs/agents/sync-points.md`](docs/agents/sync-points.md) before committing.

## Directory map

```
soc/                    Veryl RTL: board_top (PLL), soc_top + peripherals directly here,
  cpu/                  CPU core submodules
  uart/                 UART controller submodules
firmware/               Cargo workspace (virtual manifest)
  boot_manager/         Boot Manager crate (package name: boot_manager)
  resident_loader/      Resident Loader crate (package name: resident_loader)
  fw_common/            Boot Manager logic without MMIO, host-tested (make test-fw-host)
  hw_test/              Board self-test in place of the Boot Manager (make hw-smoke)
hack_demo/              Standalone Hack 16-bit C/asm demo app (toolchain self-test)
zephyr_workspace/       Zephyr west module: board/SoC/driver/dts support for "vux9k"
  app/                  Zephyr Rust demo (C glue + rust_demo staticlib; see "Zephyr" below)
sim/                    cocotb/pytest RTL testbenches
  unit/                 cocotb unit tests (single RTL module each)
  integration/          cocotb SoC-level integration tests + sdcard_model.py/virtual_serial.py
  emu/                  pytest tests on the Rust emulator + vux9k.py helpers
  sd_transcripts/       SD exchanges both card models must reproduce
emu/                    Rust emulator (core, CLI vux9k-emu, pyo3 module vux9k_emu)
coverage/               thresholds.toml (make coverage-fw)
scripts/                Build/CI plumbing only (elf2bin.py, run_riscv_tests.py, ...)
tools/                  End-user CLI: vux_tool.py (UART flashing/diagnostics/monitor)
vendor/                 bc_clone_rs submodule; riscv-tests (fetched on demand)
docs/agents/            Topic guides for agents (this file indexes them)
build/                  ALL generated/build output (gitignored) — see below
```

Everything under `soc/`, `firmware/`, `hack_demo/`, `zephyr_workspace/`, `scripts/`,
`tools/` is source. Nothing generated should ever be written outside `build/`
except the handful of firmware-hex symlinks described below
(docs/agents/layout.md, "`build/`").

## Rules for every change

- **Generated output goes to `build/` only.** The root `firmware.hex`/`firmware_d0-3.hex`
  symlinks are the one exception and must stay: `soc_ram`'s `$readmemh` bakes the boot
  firmware into the bitstream through them (layout.md).
- **An RTL commit is either a refactor proven with `make eqy` or a tested behavior change,
  never both** (rtl-workflow.md, "Step 3 commit rule").
- **The emulator follows the RTL**: a behavior change carries the emulator change and a
  lockstep program in the same piece of work; a red `sim-lockstep` is a finding
  (rtl-workflow.md, "The emulator must follow the RTL").
- **Don't raise the clock on the strength of STA**; only the fixed-placement board test
  (`make timing` + `make hw-smoke`) counts (clock-and-board.md, "The SoC clock").
- **The extended profile and the `vux9k/vux9k/ext` Zephyr board are emulator-only.** Never
  present anything that only runs there as running on the board.
- **Lost board UART output: check the USB topology (`lsusb -t`) before the firmware**
  (clock-and-board.md, "Board UART output that stops").
- **Bugs a test exposes are pinned as `xfail(strict=True)`** with the reason until fixed;
  expected-failure lists are strict (an XPASS fails the run). Never loosen a check, lower a
  coverage threshold, or remove a harness self-test to get a run through.
- **Generated or random tests get a step cap and a timeout** (`timeout_time` on cocotb tests
  that wait in a loop): a test that hangs takes a shared CI machine with it.
- **Committed files hold nothing machine-specific** (host names, local devices, absolute
  paths; `scripts/check_no_absolute_paths.py` checks the last).

## Checks that run on their own

- `make check` (CI, and `.githooks/pre-commit` on the working tree, not just what is
  staged): absolute paths, `scripts/check_agent_docs.py` (the paths and cited sections of
  these guides exist), `veryl fmt --check` + `veryl check`, ruff format/check, mypy,
  `cargo fmt --check`.
- `make check-rtl-syntax` (about a second): the generated SoC read by Yosys and compiled by
  Icarus. It catches the Veryl constructs those tools reject that `veryl build` accepts
  (veryl.md, "Veryl constructs the toolchain rejects").
- Claude Code (`.claude/settings.json`, scripts in `.claude/hooks/`): after an edit, ruff +
  mypy or `veryl check` on what was written; when the agent stops, the changed files are
  formatted and the applicable checks above run, plus `make lint-rtl` and `cargo check` of
  touched crates (one lock across worktrees, nice'd, 90 s budget, a timeout passes); writes
  into `build/`, `vendor/`, `target/` and the root `firmware*.hex` are refused. Other agents:
  run `make check` and, for Veryl, `make check-rtl-syntax lint-rtl` yourself.

## Test tiers (details: test-tiers.md)

| Tier | What | When |
|---|---|---|
| `make check`, `check-rtl-syntax`, `lint-rtl` | static checks | every change (seconds) |
| `make test-sim` | everything in CI per push: unit RTL + GLS, ISA (riscv-tests, ACT4) RTL/GLS/emu, emulator, firmware host tests + size, coverage-fw/fcov, lockstep, SoC fast/mmio/boot/hack/sd-quirks/hw-flow, Zephyr demo RTL, GLS fast | before merging |
| `make test-slow` | GLS flashing flow, Icarus full flow, long lockstep, Zephyr demo GLS, unit tests with a new random seed | nightly; by hand after touching the flashing/SD chain (sync-points.md) |
| `make sta`, `make timing`, `make eqy` | timing; RTL refactor proof | RTL changes |
| `make hw-smoke`, `make test-hw` | the board | after RTL changes, before flashing or releasing |
| `make mutation`, `make coverage` | test quality | by hand (quality.md) |

## Index: read before touching

| Area / files | Guide |
|---|---|
| `soc/**/*.veryl`, `sim/*.veryl`, `soc/verilator_lint.vlt` | [veryl.md](docs/agents/veryl.md) |
| Any RTL commit; `emu/`; eqy, synthesis flags, `make timing`, PnR seeds | [rtl-workflow.md](docs/agents/rtl-workflow.md) |
| The clock (`soc_pkg`, `board_top`), tick constants, the board, `hw_test`, `hw_smoke.py`, `test_hardware.py` | [clock-and-board.md](docs/agents/clock-and-board.md) |
| `sim/` (cocotb unit/SoC tests, the runner, Verilator) | [sim.md](docs/agents/sim.md) |
| Which tests run where; the long flashing-flow tests | [test-tiers.md](docs/agents/test-tiers.md) |
| CPU ISA behavior, CSRs, `tb_hex_runner`, `scripts/run_riscv_tests.py`, `scripts/{riscv_tests,act4}/` | [isa-tests.md](docs/agents/isa-tests.md) |
| `firmware/`, the slot format in `tools/vux_tool.py`, firmware coverage | [firmware.md](docs/agents/firmware.md) |
| `zephyr_workspace/` | [zephyr.md](docs/agents/zephyr.md) |
| `make dist`, releases, `.github/workflows/`, `docs/APP_DEVELOPMENT.md` | [dist-release.md](docs/agents/dist-release.md) |
| `make mutation`, `make coverage`, `coverage/thresholds.toml` | [quality.md](docs/agents/quality.md) |
| `build/` layout, moving files, `Makefile` paths, the post-move checklist | [layout.md](docs/agents/layout.md) |
| `.claude/` (hooks, agents, skills), `.githooks/`, these guides; when they need review | [agent-tooling.md](docs/agents/agent-tooling.md) |
| What has to change together | [sync-points.md](docs/agents/sync-points.md) |

A new guide gets `paths:` frontmatter (the files it is about), a symlink in `.claude/rules/`
and a row here; `make check` verifies the first two. Don't replace the symlink with a rule
that `@import`s the guide: Claude Code expands imports at session start, so every guide would
load into every session.
