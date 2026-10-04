---
paths:
  - "Makefile"
  - ".github/workflows/ci.yml"
  - "sim/integration/**"
  - "tools/vux_tool.py"
  - "firmware/boot_manager/**"
  - "firmware/resident_loader/**"
---

# Test tiers

What `test-sim` and `test-slow` cover, and which long tests to run by hand for which change. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Test tiers: what `test-sim` does and doesn't cover

`make test-sim` (every push/PR in CI) runs the unit suites (RTL + GLS), `test-isa`
(+ `test-isa-gls`), and the SoC tests: `sim-soc-fast` (+ `-icarus`), `sim-soc-mmio`,
`sim-boot` (CLI), `sim-hack-rtl`, `sim-sd-quirks`, `sim-hw-flow` (RTL flashing flow)
and `sim-soc-gls-fast`. `make test-slow` (CI nightly + manual `workflow_dispatch`) adds
`sim-gls-hw-flow`, `sim-hw-flow-icarus`, `sim-lockstep-slow`, `sim-zephyr-demo-gls` and
`sim-unit-random` (the unit tests with the date as random seed). Until 2026-09 the flashing flow ran in
no tier at all (25–35 min RTL / many hours GLS on Icarus); on Verilator it's ~16 s
RTL / ~3–6 min GLS, which is what made it affordable per PR.

`test_soc_hardware_flow.py` is the only test that drives the full multi-step UART
flashing protocol end-to-end through `tools/vux_tool.py`'s `build_vux9_image()`:
`'w'`-command flash of a Hack and a RISC-V payload (slot-ID handshake →
sector-count handshake → per-sector streaming), a byte-exact check of what
reached the SD model, boot-header verification via `s1`, and booting the flashed
payload through the Resident Loader. The other CLI commands are
`test_soc_boot.py`'s job.

Also run `sim-gls-hw-flow` (not in `test-sim`) explicitly whenever you touch:
- the UART flashing handshake (`firmware/boot_manager/src/main.rs`'s
  `write_sectors_from_uart()` and its `[READY]`/`[READY-SLOT:N]`/
  `[READY-COUNT:N]`/`[READY-SEC:i]` protocol),
- `tools/vux_tool.py`'s `build_vux9_image()` or the sector/header layout it
  produces,
- the SD boot/slot-loading chain (`resident_loader`, `slot_sector()`), or
- anything under `sim/integration/` that talks to the virtual UART/SD models
  (`sdcard_model.py`, `virtual_serial.py`) — a change there can silently
  desync `test_soc_hardware_flow.py`'s own protocol assumptions from the
  firmware's actual behavior (this happened during the 2026-09 `sim/` reorg:
  the test's handshake had drifted from a 2-step to a 3-step protocol
  sometime after the VUX9 v3 boot header work, undetected until this test was
  actually run — `sim-soc-fast`'s lighter flow never exercises the `'w'`
  command at all).

It's also worth running once as a final check after any `sim/` directory
layout change (as opposed to content change), even though the fast tests all
pass, precisely because it is the one test exercising the longest real
UART/SD interaction chain and is therefore most likely to surface a subtle
path- or timing-related regression the fast tests can't reach.
