---
paths:
  - "firmware/**"
  - "tools/vux_tool.py"
  - "sim/emu/test_vux9_header.py"
  - "sim/emu/test_upload_v2.py"
  - "sim/emu/test_fat_images.py"
---

# Firmware (the two-stage boot's stages, fw_common, fw_drivers)

Read before changing anything under `firmware/` or the file format in `tools/vux_tool.py`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Cargo workspace gotcha: rustflags paths are workspace-root-relative

`firmware/` is a Cargo workspace: `stage1` (the first stage, the boot ROM in I-RAM,
`build/firmware/firmware.{bin,hex}`), `stage2` (a provisional second stage, linked into
the PSRAM and wrapped as the v4 file `build/firmware/stage2/STAGE2.BIN`), `hw_test`,
`fw_drivers` (the MMIO drivers they share) and `fw_common` (the hardware-independent
logic, no MMIO, tested on the host with `cargo test -p fw_common --target <host triple>
--features fat-write` — `make test-fw-host`; `make firmware-size` holds the first stage to
the 16 KB it runs from).
**When you build a member from within its own directory (`cd firmware/stage1
&& cargo build`), rustc/the linker still runs with cwd = the workspace root
(`firmware/`), not the member directory.** This means each member's
`.cargo/config.toml` `-C link-arg=-T<path>` linker-script flag must be written
relative to `firmware/`, e.g. `-Tstage1/bootstrap/link.x`, **not**
`-Tbootstrap/link.x`. Getting this wrong produces `rust-lld: error: cannot find
linker script` with no indication of *why* the cwd changed. All members' output
binaries land in the single shared `firmware/target/riscv32i-unknown-none-elf/
release/`.

## Firmware rules (the stages, `fw_common`, `fw_drivers`)

- **Addresses come from `fw_common::map`** (MMIO, the second stage's window
  `STAGE2_BASE`, the first stage's data `S1_RAM_BASE`, mtime rate); the MMIO drivers
  (UART, GPIO, mtime, SD) are in `fw_drivers`, which also implements `fw_common`'s traits
  for them; logic that needs no MMIO goes into `fw_common` behind a trait and gets a host
  test (`upload` is transfer v2 over `Host`/`UploadSink`; `boot::RamBoot` is the first
  stage's RAM boot). The emulator's SD card is lenient about SDSC/SDHC addressing unless
  `strict=True`: use strict for anything card-type related.
- **The first stage is the boot ROM**: it is in the bitstream (BSRAM, `$readmemh` of
  `firmware.hex`), so a board only gets a new one with a new bitstream; the card can't
  update it. It loads `\VUX9K\STAGE2.BIN` (else `STAGE2.OLD`) into the PSRAM and starts it
  with a0 = SDHC, a1 = the FAT32 partition's LBA (0: none mounted), a2 = `STAGE1_VERSION`;
  S2 held or a key within `AUTOBOOT_MS` of its banner keeps it at its prompt (`s1> `).
  Its data and stack are in the PSRAM (`S1_RAM_BASE`), never in D-RAM (`hw_smoke.py`
  needs D-RAM's preload all zero; D-RAM goes away in B4b of the roadmap). It doesn't write
  the card: installing a second stage is the second stage's job (`fat::stage2::install`),
  after a RAM boot (`u`) of it if need be. Keep it small: 14 KB of 16 KB, and a later ROM
  may be 8 KB. Its `t` and `d` commands were dropped for room; what a feature costs is
  measured by building without it, and `Volume::open`'s path parsing alone cost 1.3 KB (the
  stage finds `\VUX9K\` with `stage2::dir`, by its 8.3 name).
- **Raise `STAGE1_VERSION` with every first-stage change** (`firmware/stage1/src/main.rs`):
  the second stage gets it in a2, `make dist` puts it in MANIFEST.json, and the tests read it
  from there (`sim/emu/s1_env.py`, `sim/integration/soc_env.py`, `scripts/test_hardware.py`);
  don't hard-code it.
- **Code in the PSRAM is slow**: every fetch waits ~35 cycles (no cache before B4a of the
  roadmap). The second stage's transfer loop lost UART bytes at 115200 baud until the
  receive stopped calling `mul_u32` for its timeout on every byte (`fw_drivers`'
  `Uart::read_byte_timeout` returns a byte that is there at once; `ms_ticks` shifts).
  Keep per-byte paths of code that runs from the PSRAM free of loops and calls.
- **A program started after the first stage's output may find the UART's TX FIFO full**:
  wait for `UART_TX_FULL` to clear before each byte (a store to a full FIFO is dropped).
- **The VUX9 header exists twice**: `fw_common::header` (the firmware reads it) and
  `ImageHeader`/`build_vux9_v4_image`/the constants in `tools/vux_tool.py` (it writes the
  files; the dist ships it as one file, so nothing is generated). Change both, then
  regenerate `firmware/fw_common/tests/vux9_v4_vectors/` (`VUX9_VECTORS_UPDATE=1 pytest
  sim/emu/test_vux9_header.py`): both parsers are tested against those files, and the
  pytest also compares the constants and field offsets. The old v3 slot header
  (`vux9_vectors/`) has no user left and goes next.
- **Transfer v2 exists twice**: `fw_common::upload::upload_v2` (the device) and
  `vux_tool.send_upload_v2` (the host). `firmware/fw_common/tests/upload_v2_vectors/` holds
  transcripts from a reference device in `sim/emu/test_upload_v2.py`
  (`VUX9_VECTORS_UPDATE=1` regenerates them): the host test replays their host bytes into
  `upload_v2` and compares its lines, the pytest runs the sender against their device
  lines. Change both sides and the reference device together.
- **`fw_common::fat` is tested on images from the real tools**: `tests/fat_images/` holds
  card images that mkfs.fat and mtools made and that `vux_tool.build_fat32_image` (the
  `mkfat` command) makes (stored sparse, with a `.files` list of what must read back),
  rebuilt and checked with `fsck.fat` and mtools by `sim/emu/test_fat_images.py`
  (`make test-fat`, which fails instead of skipping without dosfstools/mtools;
  `VUX9_VECTORS_UPDATE=1` rewrites them, byte-for-byte reproducibly). The same test runs
  fw_common's writes on every image through the `fat_host` example and runs `fsck.fat`
  after each; its only note allowed then is FSInfo's free count being unknown, which is
  what fw_common sets. mtools before 4.0.49 (CI's Ubuntu 22.04 has 4.0.32) takes a FAT32
  volume of fewer than 65525 clusters, as these images are, for FAT16 and fails on it:
  there the small images get fsck.fat and fat_host only, mtools checks a full-size
  vux_tool image, and rebuilding the `mkfs_*` images is skipped (it needs the mtools they
  were made with, `FIXTURE_MTOOLS`; rebuild them locally). The host tests
  (`tests/fat.rs`) read them and break copies of them one field at a time. A volume is
  FAT32 by its BPB (`FATSz16 == 0`), so the images can be a few MB; mkfs.fat then keeps the
  sector count in TotSec16. Callbacks are `&mut dyn FnMut`: generic ones gave each call
  site its own copy of the loops, which costs firmware size and split the host coverage
  per instantiation.
- **Writes are ordered for power loss** (feature `fat-write`, `fat/write.rs`): a new file's
  data and chain reach the card before its directory entry, a removed file's entry goes
  before its chain, so an interrupted change only leaves clusters nothing uses.
  `fat::stage2::install` replaces `\VUX9K\STAGE2.BIN` through `.NEW` and `.OLD` so that
  one of them is always bootable for `select_boot` (a `.BIN` that doesn't load is replaced
  without touching `.OLD`); `tests/fat.rs` cuts the card off after every possible write of
  an install, and of the install after a cut one, and checks exactly that, that every
  chain a directory points to stays valid, and that the next install recovers. Its
  free clusters hold old bytes where order matters (a directory cluster must be zeroed
  before it is linked). Keep a new write step inside those tests.
- **The SD SCLK divider survives a soft reset** (`0x4000_200C`; only power-on resets it).
  `SdCard::init` sets `SD_DIV_INIT` before every card init (a strict card reports a
  faster SCLK before it is ready) and `SD_DIV_FAST` after it. A second stage that takes
  the first stage's card over (`SdCard::adopt`, from a0/a1) keeps the divider as it is.

## Firmware coverage: `make coverage-fw` (emulator runs, source lines)

The emulator tests in `sim/emu/` run with `VUX9K_COV_DIR` set, so every SoC made by
`vux9k.start_soc()` records each executed (PC, ISA, instruction word).
`scripts/coverage_fw.py` credits a hit to an image only where that image holds the same
word at that PC (code loaded over the first stage's addresses stays apart).
RV32 hits map to lines through the DWARF of the firmware's `coverage` cargo profile,
whose code the target first `cmp`s against the release image; Hack hits map to the
demo's assembly, checked instruction by instruction against the binary. Output:
`build/coverage/fw/{fw.info,summary.md}`; minimums in `coverage/thresholds.toml`
(runs in `test-sim`). Rules:
- A line that can't run by design gets `cov:exclude(reason)` in a comment on that line
  (e.g. the `loop {}` after a soft-reset store, a panic handler nothing reaches). It
  counts for every image that links the line: a library line excluded for one stage's
  use (e.g. `find_entry` on a non-directory) must be unreachable from every stage measured.
  Don't use it for code that is merely untested — add the test. The provisional second
  stage is not measured until it is the real one.
- Raise a threshold when coverage rises; never lower one to get a run through.
- The Hack demo in CI is assembled from the committed `hack_demo/src/main.asm` (`hcc`
  isn't installed there). After changing `main.c`, regenerate it with `hcc -S`.
- Not measured yet: the host crates (emulator core, `fw_common` on its own) — that needs
  cargo-llvm-cov in the CI image.
