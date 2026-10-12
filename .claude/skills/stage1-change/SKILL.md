---
name: stage1-change
description: Checklist for changing the VUX9K first stage or the provisional second stage (firmware/stage1, firmware/stage2, fw_common, fw_drivers, transfer v2, the RAM boot, installing STAGE2.BIN, the v4 header) - version bump, size budget, header parsers, tests to run. Use before committing any change to the boot chain.
---

# First stage / second stage change

Read docs/agents/firmware.md first; the rows below are its rules in checklist form.

- [ ] Logic without MMIO lives in `fw_common` behind a trait, with a host test
      (`make test-fw-host`); MMIO drivers in `fw_drivers`; addresses come from `fw_common::map`.
- [ ] `STAGE1_VERSION` raised in `firmware/stage1/src/main.rs` for a first-stage change (the
      second stage gets it in a2; tests and `make dist` read it from there).
- [ ] `make firmware-size`: the first stage <= 16 KB (it is in the bitstream: measure what a
      feature costs by building without it).
- [ ] The first stage keeps its data in the PSRAM (`S1_RAM_BASE`), never D-RAM, and writes
      nothing to the card.
- [ ] Code that runs from the PSRAM (the second stage): no calls or loops on per-byte
      paths; it fetches each word in ~35 cycles.
- [ ] VUX9 header changed: `fw_common::header` and `tools/vux_tool.py` together, vectors
      regenerated (`VUX9_VECTORS_UPDATE=1 pytest sim/emu/test_vux9_header.py`). Transfer v2
      changed: both sides and `sim/emu/test_upload_v2.py`'s transcripts.
- [ ] SD init: `SD_DIV_INIT` before card init, `SD_DIV_FAST` after. Card-type logic tested
      with the strict emulator card.
- [ ] Tests: the emulator tests (`sim/emu/test_stage1*.py`), `test-sim`, and `sim-gls-hw-flow`
      by hand when transfer v2, the RAM boot, the install or the SD boot chain changed
      (docs/agents/test-tiers.md). Firmware coverage thresholds still met (`coverage-fw`).
- [ ] Board: `test-hw` / `test-hw-dist` before the change ships (a FAT32 card with `\VUX9K\`).
