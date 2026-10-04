---
paths:
  - ".claude/**"
  - ".githooks/**"
  - "scripts/check_agent_docs.py"
  - "pyproject.toml"
---

# Agent tooling: hooks, guides, agents, skills

What `.claude/`, `.githooks/` and these guides depend on in the rest of the tree, and when to
review them. Read before changing them, or when a change of yours appears in the table
below. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## What depends on what

`make check` (`scripts/check_agent_docs.py`) catches the rows marked **checked**; the others
fail quietly (a check that no longer runs, or a guide that no longer loads), so review them
when their trigger happens.

| In the tooling | Depends on | When it breaks | |
|---|---|---|---|
| Paths, `make` targets and citations in AGENTS.md, `docs/agents/`, `.claude/agents/`, `.claude/skills/` | the files, Makefile targets and section titles they name | a rename, a removed target, a retitled section | **checked** |
| Each guide's `paths:` globs | the directory layout | a glob that matches nothing stops loading its guide | **checked** (a glob that still matches *some* files but misses the moved ones is not) |
| `PY_DIRS` in `.claude/hooks/hooklib.py` | `pyproject.toml`'s ruff `include` | a new Python directory isn't linted after edits | **checked** |
| `CARGO_DIRS` and the per-crate `cargo check` in `.claude/hooks/stop_check.py` | the Rust crates (`firmware/<member>/`, `emu/`) and their targets | a new crate or a moved one isn't checked on stop | review |
| `stop_check.py` running `make check-rtl-syntax` and `make lint-rtl` | those Makefile targets | a renamed target fails every stop | **checked** (named in AGENTS.md) |
| `make check-rtl-syntax` | `SOC_RTL_SRCS`; `soc_ram`'s `$readmemh` being the only file Yosys can't read without `firmware.hex` | a new RTL module outside `SOC_RTL_SRCS` isn't parsed; a second `$readmemh` fails the target | review |
| `pre_edit.py`'s refused paths (`build/`, `vendor/`, `target/`, root `firmware*.hex`) | where generated and upstream code lives | a dependency developed in place under `vendor/` (e.g. a Veryl path dependency) can't be edited; new generated output elsewhere isn't protected | review |
| `NOISE` in `hooklib.py` | the output format of veryl and Icarus | new progress lines push errors out of the clipped hook output; changed ones let noise through | review |
| `stop_check.py`'s budget (90 s), lock and timeouts | the machine and how long the checks take | checks skipped with a note, or slow stops | review |
| `.githooks/pre-commit` (`make check`) | what `make check` runs and how long it takes | slow or noisy commits | review |
| `implementer` / `change-reviewer` bodies | AGENTS.md's rules, `sync-points.md`, `test-tiers.md` | stale instructions to agents | review |
| Skills (`rtl-refactor-proof`, `mutation-triage`, `bm-change`) | script options (`run_eqy.py --depth`, `mutation.py --replay`/`--summarize`), firmware constants and budgets | wrong commands in a procedure | paths and targets **checked**; options and numbers review |

## When to review

- **A directory moves or is added**, or a new language, crate or Python directory appears:
  `PY_DIRS`/`CARGO_DIRS`, the guides' `paths:`, `pre_edit.py`'s refused paths, the directory
  map in AGENTS.md.
- **A Makefile target is renamed or its sources change** (`SOC_RTL_SRCS`, `check`, `lint-rtl`):
  `stop_check.py`, `check-rtl-syntax`, the skills.
- **A tool pin changes in ci.yml** (veryl, OSS CAD Suite, ruff, mypy, Rust): `NOISE`, the hook
  timings, the fmt/check commands.
- **A rule changes in a guide**: `sync-points.md` and the two agents' bodies.
- **At the end of every ROADMAP stage**, and before the ones known to move things: B0
  (Hack removal deletes files and targets the guides and skills name), B2 (`vendor/` path
  dependency for the PSRAM controller vs. `pre_edit.py`; new RTL in `SOC_RTL_SRCS`), B3
  (firmware target `riscv32im`: the per-crate `cargo check`).
- **When a hook misfires** (blocks correct work, misses a mistake, or makes stops slow):
  fix the hook, not the work around it.

Checking a change to the tooling:
- `python3 .claude/hooks/<hook>.py` with a JSON event on stdin (`{"tool_input": {"file_path":
  "..."}}`, `{}` for Stop) reproduces a hook without a session.
- Whether a guide loads: a headless session, e.g. `claude -p "Read <file> (5 lines), then list
  the H1 headings of the instruction files in your context" --model haiku --allowedTools=Read`.
  Keep `.claude/rules/` entries as symlinks: an `@import` in a rule is expanded at session start.
