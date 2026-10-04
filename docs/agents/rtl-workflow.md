---
paths:
  - "soc/**/*.veryl"
  - "emu/**"
  - "scripts/run_eqy.py"
  - "scripts/run_pnr.py"
  - "sim/integration/lockstep_programs.py"
---

# RTL change workflow: eqy, emulator, timing

Read before committing any RTL change: how refactors are proven, what a behavior change must carry, and how timing is measured. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Step 3 commit rule: refactors are proven with `make eqy`, timing changes are tested

Keep every RTL commit one of two kinds, never both:
- **Behavior-preserving refactor** (renames, restructuring, dead-code removal,
  expression rewrites): must pass `make eqy EQY_BASE=<parent> EQY_TOP=unified_cpu`
  and `EQY_TOP=soc_top`. That is a proof over all inputs, not a test.
- **Behavior-changing timing work** (pipelining, extra wait states, anything that
  changes cycle-level behavior): verified with `make test-sim` plus the GLS runs.
Mixing them loses the ability to tell which half caused a regression.

`scripts/run_eqy.py` builds the base commit's RTL from a `git archive` in a temp
directory *outside* the repo (Veryl scans the whole project root, so a copy inside
it would clash), caches it as `build/eqy/gold-<sha>/`, snapshots both sides into
`build/eqy/<top>/` and runs Yosys `eqy` (sby/bitwuzla, `memory_map`), with both
sides flattened below `<top>` (~6 min per top on 4 cores). Notes:
- Because of the flattening, a refactor may change submodule ports or move logic
  between submodules; only `<top>`'s own ports have to match.
- `soc_ram` is replaced by a ports-only stub on both sides (its `$readmemh` needs
  `firmware.hex`; its RAMs are too big to prove usefully), so changes *inside*
  `soc_ram` are not covered — and neither is a change to its *ports*: the stubs'
  interfaces must match, so such a change is its own commit, verified by tests.
- eqy pairs up gold/gate signals by name before proving anything. When a change
  renames or restructures too much, it fails at that stage ("conflicting matches
  ... Failed to partition design") rather than with a counterexample — e.g. the
  trap-restore commit against its parent. Split such a refactor into smaller
  steps (or add `[match]` hints) rather than skipping the check.
- A mismatch is reported per partition ("Failed to prove equivalence of
  partition unified_cpu.hack_jump_take"); details are under
  `build/eqy/<top>/<top>/`.
- eqy runs with `[options] insbuf off`. By default eqy chains a buffer between
  every alias name of a net before partitioning, so each name can be a cut point;
  the chain follows the netlist's connection order, which differs between gold and
  gate as soon as a refactor adds a consumer to a net (typically: extracting logic
  into a new instance that reads `rf_rs1_data`/`rv_imm`). One design then split
  names the other kept together — "conflicting matches for gold bit …" at
  partitioning, or false mismatches in whole groups (`reg_file.rs1_data.*`,
  `rv_decode.imm_reg.*`). With it off, all names of a net stay one net on both
  sides; R3–R5's extractions prove equivalent with no hand-written exclusions.
  `EQY_NOMATCH="<patterns>"` still adds `gold-/gate-nomatch` lines if ever needed
  (excluding names only removes cut points: partitions grow, the proof stays sound).
- Proofs are local to each partition, so a change that is only unreachable
  because of how *another* block drives it (e.g. `fifo_sync` accepting a write
  on an empty-and-read cycle, which `uart_controller` never produces) still
  fails. Such a change is a behavior change as far as this workflow goes: test it. A partition that is truly equivalent but needs
  deeper induction than `--depth` (5) can also fail: raise the depth before
  concluding the refactor changed behavior.

## The emulator must follow the RTL

`emu/` models the SoC cycle for cycle, and `make sim-lockstep` (in `test-sim`) proves it
against the RTL: tb_soc_top.sv writes a trace in RTL builds (`VUX9K_RTL_TRACE`) and
`sim/emu/test_lockstep.py` compares every instruction and UART byte. So:
- **An RTL change that alters behavior needs the matching emulator change in the same
  piece of work**, and a lockstep program that exercises it if none does yet
  (`sim/integration/lockstep_programs.py`). A red lockstep is a finding, not noise.
- The extended profile (and the `vux9k/vux9k/ext` Zephyr board) is emulator-only.
  Nothing that only runs there may be presented as running on the board.
- The SD card exists twice (Python for cocotb, Rust for the emulator); changes go to
  both, and `sim/sd_transcripts/` (regenerated with `SD_TRANSCRIPTS_UPDATE=1 pytest
  sim/emu/test_sd_transcripts.py`) must stay green on both sides.
- Firmware bugs the tests expose are pinned as `xfail(strict=True)` with the reason
  until they are fixed, so the fix shows up as an XPASS.

## Synthesis flags (`SYNTH_GOWIN_OPTS = -nowidelut -no-rw-check`)

Both were measured on the same RTL (all 5 seeds, 2026-09): median slack -1.73 ns
without them, +2.03 ns with `-nowidelut`, +4.78 ns with both.
- `-nowidelut` keeps abc9 from packing wide muxes into MUX2_LUT5..8. Without it
  the mapping swung by hundreds of LUTs (and several ns) on two-line RTL
  changes, while cosmetic edits (renames, comments) changed nothing — so a
  per-commit timing comparison was mostly noise. With it LUT counts track the
  RTL. Behavior-neutral.
- `-no-rw-check` drops yosys' read/write collision emulation for block RAM: ~79
  flops, 67 of them for `i_mem`, whose comparator took the combinational fetch
  address (`pc_out`, i.e. `next_pc`) onto the critical path. The one collision
  this SoC can produce is a store to the next instruction's I-RAM word (the
  fetch reads that word in the store's MEM_WAIT cycle); its fetch is now
  undefined, documented as unsupported in README ("Writing code into I-RAM").
  RTL simulation still shows read-before-write, so no RTL test can see this.
Don't drop either flag to "simplify" the flow; re-measure with `make timing`
if you change them.

## `make timing`: one comparable record per RTL commit

`make timing` synthesizes, routes *every* seed (`run_pnr.py --all-seeds`: no early
stop, not even when a seed closes) and prints
cell counts (LUT/MUX2/FF/ALU/BSRAM/SSRAM), per-seed slack, best and median, and
the worst path's start/end flops; it also appends a row to
`build/timing/history.tsv`. Run it after every RTL commit of the timing work:
seeds alone move slack by ~1.7 ns, so compare best *and* median, and treat LUT
count as the steadier signal. Register names don't survive synthesis
(`synth_gowin` ends with `autoname`), so end points are shown as the flop's
`src` (generated `.sv` line range of its `always_ff`) plus the stem of its D
net's autoname (e.g. `D=instr_addr` is the PC register). `make sta` prints the
same end points for the adopted seed.

## PnR seeds: `scripts/run_pnr.py` adopts one seed and records it

`make pnr`/`sta`/`bitstream` run nextpnr once per seed in `PNR_SEEDS`, in parallel,
each into `build/synth/pnr/seed_<N>/` (`soc_pnr.json`, `soc_sta.json`,
`nextpnr.log`). The first seed to meet timing stops the others and is adopted;
a seed finishing below `PNR_ABORT_SLACK` (-1.5 ns) stops them too, since seed
jitter can't close that gap. Without a closing seed, the best finished one is
adopted. Its results are copied to `build/synth/soc_{pnr,sta}.json` and the seed
is recorded in `build/synth/pnr_seed.json`, which `make sta` prints — so
`pack.fs` and the STA report always come from one known seed. Changing
`PNR_SEEDS` re-runs PnR (stamp file); `PNR_ABORT_SLACK=none` keeps going past
hopeless seeds (but still stops at the first closing one); `make timing` routes
every seed. The seed list
is simply the first few primes: a seed only initializes nextpnr's RNG, so the
point is a fixed, documented list, not any property of the values.
