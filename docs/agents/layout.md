# Build layout and directory changes

Where generated files go, and what to check after moving files or changing build paths. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## `build/` — unified generated output

Every build artifact (Veryl `.sv`/`.map` output, firmware `.bin`/`.hex`, Hack
firmware, riscv-tests ISA test files, Zephyr's `west build` output, synthesis/PnR/
STA/bitstream files, cocotb builds and runs under `build/sim/`) lands under `build/<category>/`.
Run `veryl build --out-dir build/veryl` (or `make veryl`) and it mirrors the
source tree exactly — `build/veryl/soc/cpu/*.sv`, `build/veryl/soc/uart/*.sv`, etc.
See the `Makefile`'s `BUILD_DIR`/`VERYL_OUT_DIR`/`FIRMWARE_BUILD_DIR`/`SYNTH_DIR`
variables for the exact layout.

**Exception:** `firmware.hex` and `firmware_d0-3.hex` also exist as symlinks at
the repo root, pointing into `build/firmware/`. This is required
because `soc/soc_ram.veryl`'s `$readmemh("firmware.hex", ...)` calls use a bare
filename resolved relative to whatever directory the invoking tool's process cwd
is (yosys/nextpnr: repo root). cocotb runs don't use them: `sim/runners/sim_runner.py`
puts the same symlinks into each test's own run directory, its cwd. `$readmemh` is **not**
simulation-only, it's how the actual FPGA bitstream gets the boot firmware baked
into BRAM at synthesis time. Don't remove these symlinks or "clean up" the
duplication without also fixing the underlying `$readmemh` calls (out of scope
for a build-layout change; would require re-verifying real hardware).

## Veryl module resolution is directory-agnostic

`veryl build`/`veryl check` scan the whole project root recursively for `.veryl`
files and resolve module names (via `inst`) in one flat global namespace — there
is no `import`/`use`/`include` syntax. Moving `.veryl` files between directories
never breaks Veryl itself; only external tooling that hardcodes paths to the
*generated* `.sv` output (Makefile `read_verilog` commands, `sim/runners/sim_runner.py`'s
`RTL_SOURCES`, `scripts/run_riscv_tests.py`) needs updating.

## Verification checklist after touching build paths or directory layout

Run in this order (each depends on the previous succeeding):
```
make firmware        # Cargo workspace build -> build/firmware/
make sim-unit         # broadest RTL path coverage (26 unit test modules)
make test-isa
make build-hack        # hack_demo/
make sim-hack-rtl
make sim-hw-flow         # full UART/SD chain; longest real interaction (Verilator, ~16 s)
make synth-top          # yosys must resolve build/veryl/soc/{cpu,uart}/*.sv
make build-zephyr-demo   # Zephyr Rust demo for the real board (needs Zephyr)
python3 scripts/check_no_absolute_paths.py
git status                # confirm no stray untracked build output
```
`make sta` and `make test-hw` require real hardware/long PnR runs — skip unless
specifically investigating timing or hardware behavior.
