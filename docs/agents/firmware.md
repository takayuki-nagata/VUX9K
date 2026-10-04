# Firmware (Boot Manager, Resident Loader, fw_common)

Read before changing anything under `firmware/` or the slot format in `tools/vux_tool.py`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Cargo workspace gotcha: rustflags paths are workspace-root-relative

`firmware/` is a Cargo workspace (`boot_manager` + `resident_loader` members, plus
`fw_common`: the firmware's hardware-independent logic, no MMIO, tested on the host
with `cargo test -p fw_common --target <host triple>` — `make test-fw-host`. The Resident
Loader takes only constants and `crc32_update` from it: it has ~100 bytes left of its
2 KB, which `make firmware-size` enforces along with the Boot Manager's 14 KB).
**When you build a member from within its own directory (`cd firmware/boot_manager
&& cargo build`), rustc/the linker still runs with cwd = the workspace root
(`firmware/`), not the member directory.** This means each member's
`.cargo/config.toml` `-C link-arg=-T<path>` linker-script flag must be written
relative to `firmware/`, e.g. `-Tboot_manager/bootstrap/link.x`, **not**
`-Tbootstrap/link.x`. Getting this wrong produces `rust-lld: error: cannot find
linker script` with no indication of *why* the cwd changed. Both members' output
binaries land in the single shared `firmware/target/riscv32i-unknown-none-elf/
release/{boot_manager,resident_loader}`.

## Firmware rules (Boot Manager, Resident Loader, `fw_common`)

- **Addresses come from `fw_common::map`** (MMIO, mailbox, RL entry, mtime rate);
  logic that needs no MMIO goes into `fw_common` behind a trait and gets a host test
  (`upload` is the `w` exchange over `Host`/`Disk`; a 10/13-sector upload bug hid in
  the Boot Manager until it moved there). The emulator's SD card is lenient about
  SDSC/SDHC addressing unless `strict=True`: use strict for anything card-type related.
- **The VUX9 header exists twice**: `fw_common::header` (the firmware reads it) and
  `SlotHeader`/the constants in `tools/vux_tool.py` (it writes the slots; the dist ships
  it as one file, so nothing is generated). Change both, then regenerate
  `firmware/fw_common/tests/vux9_vectors/` (`VUX9_VECTORS_UPDATE=1 pytest
  sim/emu/test_vux9_header.py`): both parsers are tested against those files, and the
  pytest also compares the constants and field offsets. The Resident Loader reads fields
  by `OFF_*` and, unlike `SlotHeader::parse`, ignores the valid flag (no room).
- **Raise `BOOT_MGR_VERSION` with every Boot Manager change**: boards install a slot-0
  image only if its header version is greater. Tests read it from `main.rs`
  (`sim/emu/bm_env.py`, `scripts/test_hardware.py`); don't hard-code it.
- **The Resident Loader reads a slot twice** (check pass with CRC32, then load pass),
  because lower I-RAM holds the running Boot Manager until the load pass overwrites
  it: errors E1-E5 return to it intact, only a load-pass failure (E6) stops. Keep new
  checks in the first pass. For size, watch what the compiler links in: a
  zero-initialized buffer that is then fully overwritten cost a 152-byte `memset`
  (the header buffer is `MaybeUninit` for that reason).
- **The SD SCLK divider survives a soft reset** (`0x4000_200C`; only power-on resets it).
  The Boot Manager sets `SD_DIV_INIT` before every card init (a strict card reports a
  faster SCLK before it is ready) and `SD_DIV_FAST` after it; the Resident Loader reads at
  whatever the Boot Manager left and never touches the divider.
- **Mailbox requests are marked** (`0xB007_xxxx` launch, `0xA55A_xxxx` update); any
  other value makes the RL restart the Boot Manager. Keep slot in bits 7:0 and SDHC in
  bit 8: older RLs in flashed bitstreams read those.

## Firmware coverage: `make coverage-fw` (emulator runs, source lines)

The emulator tests in `sim/emu/` run with `VUX9K_COV_DIR` set, so every SoC made by
`vux9k.start_soc()` records each executed (PC, ISA, instruction word).
`scripts/coverage_fw.py` credits a hit to an image only where that image holds the same
word at that PC (code loaded from SD over the Boot Manager's addresses stays apart).
RV32 hits map to lines through the DWARF of the firmware's `coverage` cargo profile,
whose code the target first `cmp`s against the release image; Hack hits map to the
demo's assembly, checked instruction by instruction against the binary. Output:
`build/coverage/fw/{fw.info,summary.md}`; minimums in `coverage/thresholds.toml`
(runs in `test-sim`). Rules:
- A line that can't run by design gets `cov:exclude(reason)` in a comment on that line
  (e.g. the `nop` after the Resident Loader's soft-reset store). Don't use it for code
  that is merely untested — add the test.
- Raise a threshold when coverage rises; never lower one to get a run through.
- The Hack demo in CI is assembled from the committed `hack_demo/src/main.asm` (`hcc`
  isn't installed there). After changing `main.c`, regenerate it with `hcc -S`.
- Not measured yet: the host crates (emulator core, `fw_common` on its own) — that needs
  cargo-llvm-cov in the CI image.
