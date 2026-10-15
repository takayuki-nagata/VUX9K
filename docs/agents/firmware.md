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
`build/firmware/firmware.{bin,hex}`), `stage2` (the second stage, file manager and
launcher, linked into the PSRAM and wrapped as the v4 file
`build/firmware/stage2/STAGE2.BIN`; built with the `coverage` profile, see "Firmware
coverage"), `hello` (the hello demo, `\VUX9K\APPS\HELLO.BIN` of `make apps`: an
application linked at the PSRAM's start that the second stage's `run` starts in place; it
prints a0-a2 and soft-resets on `r`), `hw_test`,
`fw_drivers` (the MMIO drivers they share) and `fw_common` (the hardware-independent
logic, no MMIO, tested on the host with `cargo test -p fw_common --target <host triple>
--features fat-full` and again with `fat-write` alone — `make test-fw-host`; `make
firmware-size` holds the first stage to the 16 KB it runs from and prints the second's
size, 35 KB, which a RAM boot sends at ~1550 cycles a byte on the RTL).
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
  needs D-RAM's preload all zero, and D-RAM is to go away). It doesn't write
  the card: installing a second stage is the second stage's job (`fat::stage2::install`),
  after a RAM boot (`u`) of it if need be. Keep it small: 14 KB of 16 KB, and a later ROM
  may be 8 KB. Its `t` and `d` commands were dropped for room; what a feature costs is
  measured by building without it, and `Volume::open`'s path parsing alone cost 1.3 KB (the
  stage finds `\VUX9K\` with `stage2::dir`, by its 8.3 name).
- **Raise `STAGE1_VERSION` with every first-stage change** (`firmware/stage1/src/main.rs`):
  the second stage gets it in a2, `make dist` puts it in MANIFEST.json, and the tests read it
  from there (`sim/emu/s1_env.py`, `sim/integration/soc_env.py`, `scripts/test_hardware.py`);
  don't hard-code it.
- **Raise `STAGE2_VERSION` with every second-stage change** (`firmware/stage2/src/main.rs`):
  the Makefile's `mkv4 --version` reads it from there into `STAGE2.BIN`'s header, the
  banner (`VUX9K Stage 2 (vN)`) and `info` show it, and `sim/emu/s1_env.py` reads it.
- **The second stage is a line prompt** (`s2> `, echo and backspace; `fw_common::shell`):
  the commands are `firmware/stage2/src/shell.rs`'s `HELP` and `CARD_CMDS`, an error is one
  `[ERR] ` line, `put`/`install` speak transfer v2 after their line, `install`'s result is
  `[INSTALL] ...` (`install -u`: `[UNCHECKED] ...`). `tools/vux_tool.py` (`s2_cmd`, `put`,
  `run_app`, `install_stage2`, `enter_s1`'s `reset`), `scripts/test_hardware.py` and the
  emulator tests (`sim/emu/test_stage2.py`, `s1_env.s2_cmd`) depend on those strings.
- **The launcher writes I-RAM only after the CRC checked** (`fw_common::launch`, `run`): a
  file loads within `LOAD_WINDOW` through `staging` (I-RAM addresses into the PSRAM at
  `SCRATCH`), `plan` then picks the target from the checked header (RV32 in
  `XMEM_BASE..STAGE2_BASE`: the PSRAM, a jump with a0-a2 as the first stage hands them over;
  Hack: I-RAM, 16 KB, at a word; RV32 anywhere else, at 0 too, is out of the window: the I-RAM
  RV32 target went with the second stage v3), and only then does the second stage copy a Hack
  program into I-RAM (whole words, the last one's bytes past the payload 0), zero D-RAM, set
  `GPIO_BOOT_MODE` and soft-reset. So a refused file leaves the first stage in I-RAM. The windows are spelled out in
  `put_run_error`'s text (a const assert).
- **`fw_common` code that only the second stage needs stays out of the first stage's
  build**: merely adding `launch` to `lib.rs` changed the first stage's binary (36 bytes), so
  it is behind `fat-full`, as is the nibble-table `crc32_update` (the bitwise one, which the
  first stage keeps, cost ~1,800 cycles a byte from the PSRAM). Check
  `build/firmware/firmware.hex` is unchanged after any `fw_common` change.
- **Code in the PSRAM is slow**: every fetch waits ~35 cycles (there is no cache in front of
  it). The second stage's transfer loop lost UART bytes at 115200 baud until the
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
  pytest also compares the constants and field offsets.
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
  fw_common's writes (install, remove, mkdir, rmdir, move) on every image through the
  `fat_host` example and runs `fsck.fat` after each, comparing its free-cluster count with
  `free_clusters`; its only note allowed then is FSInfo's free count being unknown, which is
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
- **`fw_common::fat`'s features**: none for the first stage (it must not grow: check its
  binary is unchanged after touching `fat/`); `fat-write` (create, rename, remove,
  `stage2::install`); `fat-full` (with `fat-write`; the second stage's file manager):
  `fat/names.rs` (`list` with long names, read only and ASCII with `?`; `resolve`,
  `resolve_parent`, `locate` for paths with `.` and `..`, 8.3 name first, then long name)
  and `mkdir`, `rmdir`, `move_to`, `replace` (the second stage's `put`), `free_clusters`,
  and `install` making a missing `\VUX9K\`. New entries get 8.3 names only. What the first stage prints for an error
  (`boot::put_boot_error`) is in its binary, so `FatError::NotEmpty` exists only with
  `fat-full`.
- **Writes are ordered for power loss** (feature `fat-write`, `fat/write.rs`): a new file's
  data and chain reach the card before its directory entry, a removed file's entry goes
  before its chain, so an interrupted change only leaves clusters nothing uses. With
  `fat-full`: a new directory's cluster is zeroed, given `.`/`..` and linked before its
  entry; a removed one's entry goes before its chain; a moved file's new entry is written
  before the old one is removed (a cut can leave it under both names, never under none;
  the two then share one chain, and removing either frees the other's clusters — nothing
  detects that yet, `fsck.fat` on a PC does). `replace` writes the new file as
  `~PUT.TMP` before it removes the old one and renames it (a full card or a cut never
  leaves the name with nothing: the old bytes, or the new ones as `~PUT.TMP`).
  `fat::stage2::install` replaces `\VUX9K\STAGE2.BIN` through `.NEW` and `.OLD` so that
  one of them is always bootable for `select_boot` (a `.BIN` that doesn't load is replaced
  without touching `.OLD`); `tests/fat.rs` cuts the card off after every possible write of
  an install, and of the install after a cut one, and checks exactly that, that every
  chain a directory points to stays valid, and that the next install recovers. Its
  free clusters hold old bytes where order matters (a directory cluster must be zeroed
  before it is linked). Keep a new write step inside those tests (mkdir, rmdir, move_to and replace have theirs in
  `tests/fat.rs`'s `full` module, which also checks every directory's `.` and `..`).
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
  Don't use it for code that is merely untested — add the test.
- The second stage (`stage2`) is measured from its `coverage` ELF, which is also what
  `STAGE2.BIN` is made from: with line tables rustc ordered its functions differently from
  the release build. The emulator's coverage key keeps 31 bits of the PC, so
  `coverage_fw.py` puts bit 31 back for code in the PSRAM.
- Raise a threshold when coverage rises; never lower one to get a run through.
- The Hack demo in CI is assembled from the committed `hack_demo/src/main.asm` (`hcc`
  isn't installed there). After changing `main.c`, regenerate it with `hcc -S`.
- Not measured yet: the host crates (emulator core, `fw_common` on its own) — that needs
  cargo-llvm-cov in the CI image.
