---
name: bm-change
description: Checklist for changing the VUX9K Boot Manager or Resident Loader (firmware/boot_manager, firmware/resident_loader, fw_common, the UART flashing protocol, slot loading) - version bump, size budgets, header parsers, tests to run. Use before committing any Boot Manager or Resident Loader change.
---

# Boot Manager / Resident Loader change

Read docs/agents/firmware.md first; the rows below are its rules in checklist form.

- [ ] Logic without MMIO lives in `fw_common` behind a trait, with a host test
      (`make test-fw-host`); addresses come from `fw_common::map`.
- [ ] `BOOT_MGR_VERSION` raised in `firmware/boot_manager/src/main.rs` (boards only install a
      greater version from slot 0; tests read it from there).
- [ ] `make firmware-size`: Boot Manager <= 14 KB, Resident Loader <= 2 KB (about 100 B left;
      watch for a `memset` from zero-initialized buffers).
- [ ] Resident Loader: new checks go into the first (check) pass; only a load-pass failure (E6)
      may stop. Mailbox markers and bit layout stay compatible with older loaders in flashed
      bitstreams.
- [ ] VUX9 header changed: `fw_common::header` and `tools/vux_tool.py` together, vectors
      regenerated (`VUX9_VECTORS_UPDATE=1 pytest sim/emu/test_vux9_header.py`).
- [ ] SD init: `SD_DIV_INIT` before card init, `SD_DIV_FAST` after; the RL never touches the
      divider. Card-type logic tested with the strict emulator card.
- [ ] Tests: the emulator tests (`sim/emu/`), `test-sim`, and `sim-gls-hw-flow` by hand when
      the flashing handshake, `build_vux9_image()`, the slot layout or the SD boot chain changed
      (docs/agents/test-tiers.md). Firmware coverage thresholds still met (`coverage-fw`).
- [ ] Board: `test-hw` / `test-hw-dist` before the change ships.
