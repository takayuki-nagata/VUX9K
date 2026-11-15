---
paths:
  - "linux_workspace/**"
  - "sim/emu/test_linux.py"
  - "scripts/make_linux_dist.py"
  - "scripts/hw_linux.py"
  - "sim/integration/linux_checks.py"
  - "firmware/fw_common/src/linux.rs"
  - "sim/integration/linux_stub.py"
  - "emu/crates/vux9k_emu/src/profile.rs"
  - "emu/crates/vux9k_emu/src/csr.rs"
  - "emu/crates/vux9k_emu/src/periph/clint.rs"
  - "emu/crates/vux9k_emu/src/periph/uart.rs"
  - "emu/crates/vux9k_emu/tests/linux.rs"
  - "emu/crates/vux9k_emu/src/sdcard.rs"
  - "emu/crates/vux9k_emu/src/periph/sdspi.rs"
  - "sim/integration/sdcard_protocol.py"
  - "sim/emu/test_sd_transcripts.py"
  - "soc/sdcard_spi.veryl"
---

# noMMU Linux (linux_workspace/, the kernel on the emulator)

Read before changing `linux_workspace/`, `make linux-src`, `linux-rootfs`, `linux`, how
the emulator starts a kernel (`Soc::load_linux`, `vux9k-emu --linux`) or how the second
stage's `linux` does (`fw_common::linux`). Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## What exists, and where it runs

- `make linux-src` unpacks the kernel's (`LINUX_VERSION`, 7.3) and buildroot's
  (`BUILDROOT_VERSION`) release tarballs into `vendor/` (sha256-pinned, `fetch_archive`).
- `make linux-rootfs` runs buildroot out of its tree into `build/buildroot/` with the external
  tree `linux_workspace/buildroot/` (`vux9k_nommu_defconfig`): an RV32IMA ilp32 uClibc-ng
  toolchain that writes FLAT executables, and busybox as `rootfs.cpio`. Buildroot builds no
  kernel.
- `make linux` builds `vendor/linux-*` with that toolchain into `build/linux/`:
  `rv32_nommu_virt_defconfig` with `linux_workspace/kernel/vux9k_nommu.config` merged over it,
  the cpio built in uncompressed (`Image`), and the device tree
  `linux_workspace/dts/vux9k.dts` (`vux9k.dtb`).
- `make test-linux-emu` (`sim/emu/test_linux.py`) boots it on the emulator's `real` profile to
  busybox's shell and runs a few commands, twice: from `load_linux`, and from a FAT32 card
  (vux_tool's mkfat) through the first stage, the second stage and its `linux`, and runs the
  board's test, `scripts/hw_linux.py` (`make test-hw-linux`, test-tiers.md), against the
  emulator's CLI with `make linux-dist`'s tree. It runs only with `VUX9K_LINUX_TEST=1`, which that
  target sets, so `test-emu` (`pytest sim/emu`, CI) skips it even where an Image exists. None of
  these targets is in `test-sim` or `test-slow`. The first buildroot run takes ~40 min on a 2-vCPU
  machine, a kernel rebuild ~6 min.
- `make linux-dist` (`scripts/make_linux_dist.py`) puts the kernel as it goes on the card into
  `build/linux-dist/` (`VUX9K/LINUX/Image` and `vux9k.dtb`, this tree's programs for the card in
  `VUX9K/LINUX/BIN/` ("Userland packages"), the kernel's `linux.config`, `MANIFEST.json` with
  each card file's size and sha256 and the build's sizes for the memory table, `SHA256SUMS`;
  `sim/emu/test_make_linux_dist.py`). It is not part of
  `make dist`, which needs no Linux build. CI's job `linux` (beside `test`, so the kernel adds
  nothing to that job's time) runs `make linux-dist test-linux-emu` and on pushes uploads the
  tree as `vux9k-linux-<sha>`. It caches `build/buildroot` under an exact key (the source pins
  and `linux_workspace/buildroot/`), never a partial match: buildroot would keep the packages
  an older configuration built (below). Its size counts against the repository's 10 GB of
  Actions cache that `test`'s caches share (evicted least recently used); the unpacked kernel
  tree is fetched each run rather than cached.
- **The kernel runs on the real SoC (`Profile::Real`).** Its UART's liteuart registers
  (`periph/uart.rs`) are the RTL's (`uart_regs`), held to the RTL by the lockstep program
  `liteuart`, as its CLINT at `0x2000_0000` (`periph/clint.rs`, `timer_core`) is by `clint`.
  There is no Linux profile any more: the emulator-only one that had both ahead of the RTL
  was removed once the RTL had them. `load_linux` (`--linux`) only puts the kernel and the DTB
  into the PSRAM and starts it without firmware; `isa-test` refuses it (no PSRAM). The board
  boots the same kernel through both stages (`make test-hw-linux`, first on 2026-11-11, below).
- **The second stage boots the same files from the card** (`linux [IMAGE DTB]`, both or
  neither; `\VUX9K\LINUX\Image` and `\VUX9K\LINUX\vux9k.dtb` by default: upstream's
  `arch/riscv/boot/Image` and dtc's output, unchanged, no VUX9 header). `fw_common::linux`
  checks both headers before anything is written (the Image's magic2 `RSC\x05`,
  `text_offset` 0, an `image_size` that ends at `map::LINUX_DTB_ADDR`, a file of at most
  `map::LINUX_IMAGE_MAX`, 7 MB, below the second stage, which runs while it loads; the device
  tree's magic, `totalsize` equal to the file's size, at most `map::LINUX_DTB_MAX`, 56 KB,
  below the second stage's stack: `stage2/bootstrap/link.x` asserts its image ends below the
  slot), then reads the device tree and the Image into place and jumps with `load_linux`'s
  handoff (a0 = 0, a1 = the device tree, mie and mstatus.MIE cleared, EV_ENABLE left 0).
  `map::LINUX_DTB_ADDR` is a copy of `soc::LINUX_DTB_ADDR`: change both. Names: a PC's copy
  may give the files long names; `resolve` finds them by their 8.3 alias or long name, case
  ignored, and mkfat's upper-case 8.3 names too. The emulator tests use a stand-in kernel
  (`sim/integration/linux_stub.py`: the 64-byte header, then code that prints a0-a2, mie,
  mstatus and word sums of both files), the RTL one too (`test_soc_stage2.py`); the load of
  the real 3.2 MB Image is ~230M cycles (~70 a byte), ~13 s at 18 MHz.

## Things that are not obvious

- **Every exec copies the whole executable into contiguous RAM.** Buildroot links riscv FLAT
  with `elf2flt -r`, and the kernel's binfmt_flat then loads text and data together. Without
  `-r` the text would be mapped straight from the ramfs. That doesn't work on riscv: its PIC
  code reaches its data PC-relatively, so the two must stay at their linked distance (a
  try ended in SIGILL at an unrelocated text address). FDPIC is in the kernel but not in
  buildroot's riscv toolchain. So busybox is kept small (`board/busybox.config`: allnoconfig
  plus hush and core applets, ~207 KB; "Memory budget"), and init is this tree's own small program ("Init"),
  not busybox's. Buildroot's `busybox-minimal.config` (780 KB) needed order-8 allocations that 8 MB
  could not keep.
- **All 8 MB of the PSRAM go to the kernel.** The stages' memory (from `0x806F_0000`) is free
  once the kernel runs. The DTB sits in the top 64 KB (`soc::LINUX_DTB_ADDR`, `0x807F_0000`,
  and `fw_common::map::LINUX_DTB_ADDR` for the second stage), and the kernel reserves its pages.
- **buildroot sets `BR2_SOFT_FLOAT` for no RISC-V target.** A riscv32 without F would get
  uClibc's FPU configuration, whose riscv32 fenv needs F (`frrm`). `external.mk` replaces
  `UCLIBC_FLOAT_CONFIG` with the soft-float one. This works because uClibc's configuration
  fixups expand it only when they run, after `external.mk` is read.
- **buildroot does not rebuild a package when its configuration changes.** After editing the
  defconfig or `board/`, remove `build/buildroot/target`, the package's `build/buildroot/build/<pkg>-*`
  and every `.stamp_target_installed` before `make linux-rootfs`. A toolchain option needs
  `rm -rf build/buildroot`.
- `FORCE_UNSAFE_CONFIGURE=1` is set because host-tar's configure refuses to run as root, which
  a CI container is.
- **The kernel fragment check.** `make linux` stops if a fragment's `CONFIG_X=` value is not in
  the final `.config`. For `# CONFIG_X is not set` it stops only if X ended up set: an option
  whose dependencies are off is dropped from `.config`. In 7.3, `PREEMPT_NONE` exists only
  where `ARCH_NO_PREEMPT` is set, and riscv has lazy preemption.
- **The console's name is `liteuart`, and its tty is `ttyLXU`.** `console=ttyLXU0` matches no
  console, `/dev/console` has no driver behind it, and init prints nothing.
- liteuart polls on a timer. The DT gives it no interrupt (the UART's EV_ENABLE stays 0, so
  EV_PENDING shows EV_STATUS: the poll services RX and TX only from EV_PENDING's bits):
  wired to the hart's MEIP directly, riscv-intc's per-CPU IRQ makes `request_irq` fail, and
  the driver falls back to polling anyway. An interrupt-driven UART needs a PLIC-like controller.
- M-mode Linux still runs processes in U-mode: `mret` with MPP = U, system calls as ecalls
  from U (cause 8), and `user_mode()` reads MPP. That is why the CPU has U-mode.
- mcounteren is 0, so `cycle`/`time`/`instret` trap in U-mode. The DT leaves out `zicntr`, but
  the kernel assumes it for any DT `i` anyway: a program that reads them gets SIGILL.
- Misaligned accesses trap, as in the RTL, and the kernel emulates them
  (`RISCV_PROBE_UNALIGNED_ACCESS`). head.S writes the PMP CSRs behind a temporary trap vector
  that skips them.

## Kernel patches

The kernel is upstream's tarball plus this tree's patches, `linux_workspace/kernel/patches/*.patch`
(the first: `0001-spi-vux9k-sd.patch`, the SD card's SPI controller, below). Keep them few and
small, and prefer an upstream driver and binding where one fits (the UART is upstream's liteuart).
- `make linux` applies them in name order with `patch -p1` onto a fresh unpack, once, behind the
  stamp `vendor/linux-*/.vux9k-patched-<hash of the set>`. A tree that holds another set (a patch
  changed, was added or removed) is deleted and unpacked again first: patches are never applied
  over each other. So edit a patch, not the files in `vendor/linux-*`: an edit there is lost at
  the next unpack, and CI unpacks every run.
- Write a patch with `git diff --no-index` between a copy of the upstream files and the changed
  ones (`a/` and `b/` paths, `-p1`); `scripts/checkpatch.pl --no-tree` on it finds the kernel's
  style problems (its "Missing Signed-off-by" doesn't apply here).
- `patch` is a host tool buildroot already requires, so any machine that builds the userland has it.
- 0002 and 0004 both change `drivers/mmc/host/mmc_spi.c` (0002 the request size near the top,
  0004 the read path): 0004 is made against the file with 0002 applied.

## The SD card

- The SoC's SD port (`0x4000_2000`: data, CS, busy, divider; README's address map) is driven by
  `spi-vux9k-sd` (`CONFIG_SPI_VUX9K_SD`, the patch above, DT `vux9k,sd-spi` with
  `clock-frequency`), polled: no interrupt, every register write waits for busy to clear (the
  port ignores writes while busy, CS and divider ones too). The card on it is upstream's
  `mmc_spi` (`mmc-spi-slot`), with its data CRC on (`mmc_core.use_spi_crc`'s default).
- With no card-detect line, mmc_spi polls the card every second (CMD13, `MMC_CAP_NEEDS_POLL`).
- Both card models (sim/integration/sdcard_protocol.py and the emulator's sdcard.rs) answer
  what mmc_spi sends: CMD59, CMD9/CMD10, CMD13, ACMD51/ACMD13, CMD18 + CMD12, CMD25 with
  tokens FC/FD, real CRC16s. Their CSD leaves out the switch (CMD6) and erase classes, so Linux
  sends neither; it prints "card lacks mandatory switch function, performance might suffer".
- **CS goes high after every block mmc_spi reads**: the block's SPI message has no
  `cs_change` on its last transfer, so the SPI core deselects the card, and selects it again
  for the next block of the same CMD18 (mmc_spi's header says "many cards seem OK with
  chipselect flapping"). A card that ends a CMD18 at CS high gets one block per request: the
  models did at first, and mmc_blk retried every request a block at a time, with a CMD13
  each, so a 256 KB `dd` didn't finish in minutes. The transcript `multi_block_read` reads as
  mmc_spi does.
- **mmc's queue is small** (patches/0002-mmc-small-queue.patch): `MMC_QUEUE_DEPTH` 4 instead
  of 64, and mmc_spi's requests at most 16 blocks instead of 128. The queue allocates every
  request with its scatterlist when the card is found, unmovable slab spread over the RAM:
  with upstream's values Slab was 920 kB after the boot and the largest free block fell to
  order 6-7, so busybox's exec (order 7) failed.
- **mmc_spi looks for a block's data token 16 bytes per SPI message**
  (patches/0004-mmc-spi-token-search.patch; upstream reads one byte per message). A real card
  sends FF for a while before the token (N(AC)), and here a message costs as much as tens of
  bytes. The bytes read after the token go to the start of the block's buffer (or of its
  CRC), and the data message reads only the rest. A read never goes past the block's CRC: the
  chunk is cut to the block's length + 3. So a short block (CSD 16, SCR 8 bytes) ends where it
  did, and the next block of a CMD18 and CMD12 see the same bytes as before. 16 because the
  search's cost is per message, and 16 still fits the CSD's and CID's block; mmc_spi's
  scratch buffer would allow up to 29. Upstream's handling of the leading 00 (N(CX)), the
  timeout, a bit-shifted token and the error tokens is unchanged.
- **The emulator's card answers a read at once** unless it is told to wait:
  `sdcard::Faults::read_waits` (Python `sd_insert(read_waits=[...])`) sends that many more FF
  before each data block's token or error token, one entry per block in turn (the registers'
  blocks too). It exists in the Rust card only, like `fail_after`; the default (none) keeps
  `sim/sd_transcripts/` as they are. `test_linux.py`'s `test_card_with_read_waits` boots with
  waits that put the token before, on and after the end of mmc_spi's 16-byte reads. It checks
  an exec from the card, a write, and a dd of random sectors through Linux to the sectors after
  them, byte for byte. On that card (the emulator, 2026-11-15), with patch 0004 and without it:
  the 256 KB `dd` 15.1 s and 34.8 s, bbawk's first exec (`awk`) 5.2 s and 12.0 s, a write's
  `sync` 2.8 s and 6.6 s, the boot to the prompt 12.7 s and 15.3 s.
- **Reading the card from Linux is ~500 cycles a byte** (~36 KB/s at 18 MHz, the emulator,
  2026-11; the second stage reads at ~100): a profile of a `dd` from the card put ~27% in the
  driver's byte loop (48 cycles a byte are the wire at 3 MHz), ~16% in the SPI core (mmc_spi
  sent several one-byte messages per block, e.g. its token search, before patch 0004), ~6% in
  the CRC16 and ~32% in the timer tick and the scheduler, which take ~23% of the CPU even when
  it is idle (one HZ 100 tick is ~40k cycles here). The block device's readahead (128 KB) also reads past what a
  short sequential read asks for: a 256 KB `dd` read 496 KB.
- **What the block layer costs in RAM** (2026-11, the emulator; SLUB_TINY has no slabinfo, so
  the slab pages were counted per cache by reading `mem_map` and `slab_caches` from the
  emulator's PSRAM with `Soc.xmem_word`): kernel code 1,770K → 2,191K, Slab 368 →
  792 kB. The largest part of the slab was iomap's two ioend biosets (`BLOCK` selects
  `FS_IOMAP`), 32 `biovec-max` entries each reserved at boot, ~256 kB that vfat never uses:
  patches/0003-iomap-small-ioend-pools.patch makes them 2. The printk buffer went from 64 KB
  (264 KB with its metadata) to 16 KB (`LOG_BUF_SHIFT=14`).
- Small allocations made after the boot (an inode, a page of a FAT) land in the middle of
  free space and split the largest blocks: with ~2.4 MB free, a single page in a 512 kB block
  is enough to make busybox's exec fail. Keep the free total high, not only the largest block.
- The card is found after the driver probes, in the background (mmc's rescan work), so it can
  appear after init has started: whatever uses it at boot waits for `/dev/mmcblk0p1`.
- **Init mounts it on `/mnt/sd`** ("Init": it waits up to 5 s for `/dev/mmcblk0p1`, then mounts
  it vfat and prints `init: /dev/mmcblk0p1 mounted on /mnt/sd`, or `init: no card`).
- **Writes**: busybox has `sync` and `dd`. The stages read what vfat writes (long names
  beside the 8.3 ones, lowercase flags, deleted entries, fragmented files), but only a
  clean card: a file whose chain doesn't match its size (a write cut off) is a `BadChain` to
  `fw_common::fat`, which refuses it where fsck would mend it. Sync before power goes, and
  umount where possible; `test_linux.py`'s `test_card_after_linux` writes, umounts, runs
  `fsck.fat -n` on the card and boots the first and second stage from it.

## Init

The initramfs' `/init` is this tree's own program (`linux_workspace/userland/init/`, package
`vux9k-init`), not busybox init: on noMMU every process holds a whole copy of its executable
(FLAT, above), so busybox init kept ~370 KB of busybox in RAM for good, one of the two
order-7 blocks a boot left busy, and with the SD card's kernel that was the margin busybox's
exec needed. Init does without exec what the inittab and buildroot's `/init` script did:
- devtmpfs on `/dev` (the kernel mounts it only for a root that is not an initramfs; buildroot
  installs its `/init` script only where there is none), the console on fds 0-2, proc, sysfs
  (the LED class; nothing mounted it once busybox init and its inittab were gone);
- the SD card on `/mnt/sd` ("The SD card");
- the login shell `-/bin/sh` (it reads `/etc/profile`) in its own session with the console as
  controlling tty, started again when it exits, after a second if it lived less (a failing
  exec would loop); every orphan reaped;
- SIGTERM, SIGUSR1 and SIGUSR2 (what busybox's `reboot`, `halt` and `poweroff` send to process 1
  when it isn't busybox init): sync, umount the card, `reboot(2)`.
- the system monitor `/usr/sbin/sysmon` ("LEDs and the system monitor") beside the shell, in
  its own session, stdin `/dev/null`, its output on the console: when it dies (a signal other
  than SIGTERM/SIGINT, or a status other than 0: `init: /usr/sbin/sysmon killed by signal 11`)
  init starts it again 5 s later (`alarm`), twice as late after each end within a minute of
  its start (an exec that fails, up to 320 s); when it was stopped (`kill <pid>`, it ends with
  status 0: `init: /usr/sbin/sysmon stopped`) init leaves it so. init keeps the signals it
  waits for blocked but in `sigsuspend`: a signal between its check of their flags and its
  sleep would otherwise wait for the next child's end (an idle shell: never).
No inittab any more (`board/post-build.sh` removes it): a process that should run beside the
shell (a daemon) is started, and restarted, by init; what it does stays out of process 1.

## LEDs and the system monitor

- **The GPIO block is upstream's `gpio-mmio`** (`GPIO_GENERIC_PLATFORM`, compatible
  `wd,mbl-gpio`), twice, one 4-byte `dat` register each: the LEDs' at `0x4000_3000`
  (`ngpios = <6>`; with no direction register gpio-mmio makes every line an output, and reads
  the register back) and the button S2's at `0x4000_3004` (`ngpios = <1>`, `no-output`, label
  `button`). Never a `reg` over `0x8`/`0xC`: the soft reset's registers. The block ignores byte
  strobes, which gpio-mmio's 32-bit accesses of a 4-byte register never need.
- **The LEDs are `gpio-leds`**: `/sys/class/leds/led0`-`led5` (the DT's `label`), active high
  (the RTL inverts the pins). `led0` has the `heartbeat` trigger (faster under load), `led1`-`led4`
  none (sysmon's bar, `led1` its first), `led5` `mmc0` (the card's accesses; mmc's host
  registers that trigger itself once `LEDS_TRIGGERS` is set): the card's LED at the end of the
  row, apart from the bar, where its blinks show. `timer`, `default-on` and `panic` are built in too:
  `echo timer > /sys/class/leds/led0/trigger`. A trigger's LED changes behind `brightness`'s
  back: `brightness` reads the LED class's value, not the register; only the emulator
  (`soc.leds`) sees the register.
- **They cost ~1.7 s of the boot** (2026-11, the emulator, an A/B with and without the GPIO and
  LED options: the prompt at 12.0 s and 10.6 s): `initcall_debug` put 0.7 s into
  `gpio_mmio_driver_init` (two chips) and 1.0 s into `gpio_led_driver_init` (six LED class
  devices). Nothing waits there: every device the driver model registers costs ~0.1-0.3 s at
  18 MHz (sysfs, devtmpfs, the bus's matching; `liteuart_init` took 0.7 s,
  `param_sysfs_builtin_init` 1.3 s), and `fw_devlink=off` saved little. Count devices when
  adding any.
- **No interrupt, no input layer**: the button is a GPIO line read through the GPIO character
  device (`/dev/gpiochipN`, the chip labelled `button`, `GPIO_V2_GET_LINE_IOCTL` then
  `GPIO_V2_LINE_GET_VALUES_IOCTL`), polled. `gpio-keys` would need `INPUT` (left out for size)
  and, for edges, an interrupt the SoC doesn't route; GPIO sysfs is off (deprecated). One
  process at a time holds the line: a second sysmon says `sysmon: no button` and runs without.
- **sysmon** (`linux_workspace/userland/sysmon/`, package `vux9k-sysmon`, in the initramfs:
  init starts it at boot): every interval (`-i`, 1000 ms) one of `/proc/uptime` (the CPU's use
  since the last sample), `/proc/meminfo` (MemTotal - MemFree over MemTotal) or
  `/proc/loadavg` (1 minute) as a bar of 0-4 LEDs on led1-led4 (a percentage in quarters,
  any use lighting the first; a load at 0.25, 0.5, 1, 2); the button, read every `-b` (200 ms),
  steps cpu -> mem -> load; `-m` the first mode, `-n` a number of intervals, `-v` a line per
  interval (`sysmon: cpu 37% bar 2`) and one per drop of the page cache (`sysmon: dropped the
  page cache (no free block of order 6, Cached 1112 kB)`), `-r KB` the Cached above its floor
  that drop needs (64; `-r 0` no reclaim at all: "Memory budget"); it looks every 3
  intervals. Without `-v` a drop says nothing (init's sysmon has no
  `-v`: a line every few seconds would land in the shell's or vi's screen); a
  `/proc/sys/vm/drop_caches` it can't write it says once (`sysmon: cannot write
  /proc/sys/vm/drop_caches`) and reclaims no more. It writes an LED's `brightness` when it changes, and all
  four every 10 intervals (`REFRESH_INTERVALS`): another process (a second sysmon ending, the
  shell's `echo`) may have changed one.
  SIGTERM/SIGINT: its LEDs off, `sysmon: stopped`, status 0.
- **Rust in the FLAT userland.** uClibc has no Rust target, so sysmon is a `no_std` staticlib
  (`src/lib.rs`: the parsing, the bar, the loop behind a `Sys` trait) for the bare
  `riscv32im-unknown-none-elf` (CI's one Rust target; RV32IM code runs on RV32IMA, and the
  ilp32 soft-float ABI is the same) linked by buildroot's gcc with a C `main.c` (the system
  calls). It must be built as buildroot builds C for FLAT, **`-C relocation-model=pic -C
  code-model=medium`** (`.cargo/config.toml`): PC-relative code and pointers in data as
  `R_RISCV_32` relocations, which `elf2flt -r` turns into the file's relocations (`flthdr -p`:
  `Has-PIC-GOT`; the default code model's absolute `lui` addresses have no FLAT relocation). No
  `core::fmt` (numbers are written by hand: `Line`), no allocator, `panic = "abort"` with a
  handler that prints and `_exit`s. `make test-sysmon-host` (a dependency of
  `test-linux-emu`) runs its tests on the host with a fake `Sys`; `cargo test` there needs
  `--lib`, since the staticlib has no panic runtime on the host.

## Memory budget

What the userland can run is bounded by the **largest free block** of contiguous pages, not by
MemFree: an exec copies the whole FLAT executable into one block (above). So every stage that
changes the kernel or the initramfs records this table, from `sim/integration/linux_checks.py`
(`memory`): the Image's, the initramfs' (`rootfs.cpio`), busybox's and the card's busyboxes'
sizes ("Text tools and vi"; make linux-dist's `MANIFEST.json`, `sizes`), MemTotal and MemFree, the largest free block with its order, and the
free blocks of order 6 and up (`/proc/buddyinfo`). `make test-linux-emu` prints it for both boot
paths (`test_linux.py`'s `test_record_measurements`), the board's `hw_linux.py` as `[MEM]` lines.
- It is read right after the boot with the shell's builtins only (`MEM_PROBES`: hush's `exec`,
  `read` and `echo` over `/proc/meminfo` and `/proc/buddyinfo`). `cat` would exec busybox and
  measure memory with a copy of it in RAM. busybox has no `free`.
- **This hush drops a loop's redirection after the first pass**: `while read l; do echo $l;
  done < FILE` prints the file's first line, then its `read` waits on the console (a regular
  file in the initramfs as much as `/proc`; the lines typed next come out twice, echoed and
  then by the loop). Open the file on a descriptor and redirect each `read` instead:
  `exec 3<FILE; while read l <&3; ...; done; exec 3<&-`.
- **Reading the card leaves the free memory in pieces.** noMMU has no compaction (it needs
  an MMU), so a high-order block that page cache or a small allocation lands in stays broken
  until every page in it is freed. A 256 KB `dd` from the card (with readahead ~500 KB of page
  cache) broke the one free order-8 block after the boot (`6:4 7:1 8:1` -> `6:5 7:2 8:0`,
  MemFree 3,372 -> 2,772 kB, 2026-11); a second one and a few commands later no order-7 block
  was left, and busybox's exec, then an order-7 block, failed. So **keep busybox's exec at
  order 6**: binfmt_flat allocates text + data + max(bss + stack, relocations) in one block
  of a power of two pages, and busybox's is ~232 KB of 256 KB (`flthdr -p`: Data End, BSS End
  and its stack, 16,000 B, fixed by busybox's Makefile); `test_linux.py`'s
  `test_exec_after_card_reads` reads 1.5 MB more from the card, an exec after each.
- **sysmon drops the page cache when no free block of order 6 is left** (`Reclaim` in its
  `src/lib.rs`). An allocation above order 3 (`PAGE_ALLOC_COSTLY_ORDER`) tries reclaim once and
  then gives up without compaction (mm/page_alloc.c: `if (costly_order && (!can_compact ||
  ...)) goto nopage;`, and noMMU can't compact), so the card's page cache, clean and
  droppable, keeps the blocks it lies in split. In a spike (2026-11-15, the emulator), 2 MB
  read from the card left `5:3 6:0` (Cached 1,116 kB, MemFree 640 kB), and then busybox's
  exec failed; `echo 1 > /proc/sys/vm/drop_caches` (~20M cycles, ~1.1 s) brought back `6:2
  8:1` (Cached 520 kB, MemFree 3,352 kB). So every 3 intervals (`RECLAIM_EVERY`) sysmon reads
  `/proc/buddyinfo` and, with no block of order 6 or above, `/proc/meminfo`'s Cached; at `-r`
  kB (64) above the Cached its last drop left, or at any Cached before its first drop, it
  writes `1` to `/proc/sys/vm/drop_caches`. The file takes 1 to 4 only (`extra1`/`extra2`
  of its sysctl; a `5` is refused with EINVAL): at its start sysmon writes `4`, which drops
  nothing and leaves the kernel's `sysmon (N): drop_caches: 4` the only such line on the
  console (the later drops', the shell's writes' too, say nothing).
  - **The threshold is above a floor**: Cached is not what a drop frees, since the initramfs
    is ramfs, whose ~520 kB of pages count in it and stay. An absolute threshold under that
    is always met; one above it would also need the droppable part to pass it. So the floor
    is the Cached sysmon reads at the look after a drop (what that drop could not free), and a
    drop needs `-r` kB of cache above it. Before any drop there is no floor, so the first drop
    needs no margin. The floor is set anew after each drop, not kept at the lowest Cached seen:
    files written to the initramfs (ramfs) since, or a process that still holds what it took,
    raise what can't be dropped, and a floor that only went down would then be passed all the
    time, with a drop that frees nothing every 33 s (each one costing the card's busybox its
    cache). 64 kB, not more: the spike's pieces came with 100 to 600 kB above the floor. A
    drop that brought back no block of order 6 (the pieces are someone else's) is followed by
    30 intervals (`RECLAIM_HOLDOFF`) without one.
  - **Every 3 intervals, the files kept open**: a read of `/proc/buddyinfo` every interval
    took sysmon's CPU over an idle shell from 3.5-3.7% to 5.0-5.1% (2026-11-15, the
    emulator: ~0.15% a read). It looks every 3 s instead, and main.c keeps each `/proc` file
    it reads open and reads it again from offset 0 (seq_file writes it anew), without the
    path walk of an open. A card read that breaks the blocks takes seconds; a person typing
    the next command leaves sysmon those 3 s (the tests wait for it: `wait_large`).
  - The kernel itself never drops it; the price is that the next exec of a program on the
    card reads it from the card again. With sysmon stopped nothing reclaims:
    `test_linux.py`'s `test_sysmon` starts one again from the shell at its end.
    `test_page_cache_reclaim` stops it, reads 2 MB from the card, has sleeping busyboxes take
    the free blocks of order 6 and up that are left (every dd or sleep is an exec of busybox,
    which needs such a block: reads alone can't leave none), and wants none back from a
    `sysmon -r 0`, then one back with sysmon's `dropped the page cache` line.
- **The card's busyboxes stay at order 5** (bbawk at 6; "Text tools and vi"), and their build
  stops above it: the processes of a pipe (`grep x F | sort | uniq -c`) each hold their block
  at once, and after the card's reads the free memory is in pieces, many of order 5 and below
  and one or none of order 6 (2 MB read in the spike: `5:3 6:0`), until sysmon's drop above
  (the spike's two pipes of `PIPE_COMMANDS` both failed then, with `Unable to allocate RAM`,
  and passed after the drop). A pipe whose processes are each order 5 fits where one more
  order-6 exec would not.
- **What every program carries from libc** (each is linked statically and copied whole): the
  toolchain is uClibc built without a thread library (`BR2_PTHREADS_NONE`: with linuxthreads
  its start-up pulled `__pthread_handles`, 16 KB of data, `pthread_keys`, 8 KB, and through
  `sysconf`'s `fopen` stdio with 8 KB of buffers into every program: memprobe was ~70 KB, init
  75 KB), without unwind tables (`external.mk`'s `UCLIBC_EXTRA_CFLAGS`: libc.a's `.eh_frame`
  was ~19 KB of busybox) and with a double, not long double, for printf's and strtod's
  arithmetic (`board/patches/uclibc/` with `UCLIBC_HAS_LONG_DOUBLE_MATH` off: riscv32's
  128-bit soft-float long double pulled ~22 KB of libgcc into busybox). busybox has no `mount`
  (`board/busybox.config`): init mounts what it needs and `/proc/mounts` lists it, and the
  applet linked in libc's resolver and `getaddrinfo` (~36 KB). Measured 2026-11: busybox
  327 KB -> 207 KB, init 75 KB -> 13 KB, memprobe 71 KB -> 4 KB.
- A failed allocation of a high order is not a quiet probe: the kernel prints a page allocation
  failure, a stack trace and a dump of its memory to the console (`warn_alloc`). Don't measure the
  largest block by allocating until it fails; read buddyinfo.

## Userland packages

This repository's own programs are buildroot packages of the external tree, one per program
(the first: `vux9k-memprobe`, which `linux_checks.COMMANDS` runs):
- The source is in `linux_workspace/userland/<name>/` (with a `Makefile` that builds from
  buildroot's `CC`/`CFLAGS`/`LDFLAGS`); the package in
  `linux_workspace/buildroot/package/<pkg>/` (`Config.in` and `<pkg>.mk`: `generic-package`,
  `SITE_METHOD = local`, `SITE` the userland directory). A new one is also `source`d by the
  tree's `Config.in`, enabled in `configs/vux9k_nommu_defconfig`, and listed in the Makefile's
  `VUX9K_BR_PACKAGES` (`external.mk` includes every `package/*/*.mk`).
- **`make linux-rootfs` runs `<pkg>-rebuild` for each of `VUX9K_BR_PACKAGES`** before the build
  that writes `rootfs.cpio`: buildroot never rebuilds a package it has built (above), and for a
  local site `-rebuild` rsyncs the source again. The sources stay outside
  `linux_workspace/buildroot/`, so changing one leaves CI's exact cache key for
  `build/buildroot` alone (ci.yml) and costs a rebuild of the package, not of buildroot.
- **A FLAT executable's stack is fixed at link time** (elf2flt `-s`; nothing grows it). Give
  each package `<PKG>_FLAT_STACKSIZE` (buildroot passes it to elf2flt, `package/Makefile.in`);
  `riscv32-buildroot-linux-uclibc-flthdr -p` on the built file shows it.
- **Mind each program's size**: it is linked statically, copied whole at each exec, and in the
  initramfs it is RAM for good. uClibc's stdio pulled in by `printf`/`sscanf` made memprobe
  120 KB; with `read`/`write` and its own number parsing it is 4 KB ("Memory budget": the
  libc this toolchain has).
- **Install into the initramfs, the card, or both.** `INSTALL_TARGET_CMDS` puts the program in
  the initramfs (every byte there is RAM for good: the memory table shows it). For the card,
  `INSTALL_IMAGES_CMDS` installs it into buildroot's `images/sd/VUX9K/LINUX/BIN/`, which `make
  linux-dist` copies as it is into its tree (with each file's size and sha256 in
  `MANIFEST.json`'s `card_files`, and `SHA256SUMS`): the card's programs go with the kernel
  (`vux9k-linux-<sha>`, a release's kernel archive), not in `make dist`, which builds no Linux.
  `make_linux_dist.py` refuses anything in `images/sd/` outside `VUX9K/LINUX/BIN/`. That
  directory is on the login shell's PATH (`/etc/profile`, after the initramfs' directories), as
  `/mnt/sd/VUX9K/LINUX/BIN` once init has mounted the card ("The SD card"); `hw_linux.py` puts
  it on the board's card with the Image and the device tree, `test_linux.py` builds it into the
  emulator's card, and `linux_checks.COMMANDS` runs memprobe from there by its name and path.
  **Prefer the card** for anything not needed before init has mounted it (memprobe is there
  only), but mind that a program's first exec from the card reads it at ~36 KB/s ("The SD
  card"); busybox stays in the initramfs for that reason (its 327 KB took ~9 s at each
  boot and after the page cache drops it). Artifacts lose the
  files' modes (`upload-artifact` zips), which a FAT card doesn't keep anyway. Removing a
  package does not remove what it installed there or in `target/`: clean `build/buildroot` then.
- **A package can build busybox again from buildroot's tarball** (`vux9k-bbtools`, "Text tools
  and vi"): no `SITE_METHOD = local`, but `<PKG>_SOURCE`/`_SITE` busybox's (`BUSYBOX_VERSION`,
  `BUSYBOX_SITE`), `_DL_SUBDIR = busybox` (the download busybox has already), the tarball's
  sha256 in its own `.hash` (`BR2_DOWNLOAD_FORCE_CHECK_HASHES` refuses a file without one), and
  buildroot's `package/busybox/*.patch` applied by a post-patch hook. Its configurations and
  their Makefile stay in `linux_workspace/userland/bbtools/` (CI's cache key, above), read
  where they are; `-rebuild` builds the extracted tree again, and each run writes the
  `.config`s again, so a changed fragment is built.

## Text tools and vi

The initramfs' busybox has only what a boot and a shell need ("Memory budget": its exec's block
is order 6 with 24 KB to spare), so the text tools are **small busyboxes on the card, one for
each use** (package `vux9k-bbtools`, `linux_workspace/userland/bbtools/`), in
`VUX9K/LINUX/BIN/` with the other programs for the card ("Userland packages"):

| On the card | Applets |
|---|---|
| `bbedit` | vi, less |
| `bbgrep` | grep, egrep |
| `bbsed` | sed |
| `bbsort` | sort, uniq, cut, tr, tail |
| `bbawk` | awk |
| `bbcalc` | expr, dc |
| `bbfiles` | find, xargs, diff, cmp, stty |

- **Each is built from `common.config` and its own fragment over allnoconfig**
  (`bbconfig.sh`): busybox's kconfig resets every bool in allnoconfig, `KCONFIG_ALLCONFIG`'s
  too, and oldconfig alone takes the defaults (mostly y) of what an applet brings in, so the
  script puts the fragment's lines over allnoconfig and turns off again what each oldconfig
  pass adds that no fragment names. It stops when a fragment's line is not in the result (a
  misspelt option, or its dependencies off). head, wc and test stay the initramfs' (busybox
  and hush); an applet the initramfs has stops the install.
- **The applets' names are links in the initramfs, to the card**: vfat holds no symbolic
  links, so `/usr/bin/vi` is a link in the initramfs to `/mnt/sd/VUX9K/LINUX/BIN/bbedit`
  (from each build's `busybox.links`; the host can't run the riscv busybox to ask it). The
  exec follows it, argv[0] stays `vi`, and busybox picks the applet by it. Without the card
  they are dangling links: `not found`. The names on the card are 8.3 (the Makefile refuses a
  longer one).
- **Each exec's block has an order it may not pass**: order 5 (128 KB), bbawk's order 6
  (`VUX9K_BBTOOLS_ORDERS` in the `.mk`). `flthdr.sh` computes the block from `flthdr -p`
  (Data End + max(BSS End - Data End + the stack, 4 x the relocations) + 1 KB for the
  arguments and the environment) and stops the build when one passes its order; its table
  is `build/buildroot/images/bbtools-flthdr.txt`. Why order 5: a pipe's processes each need
  their block at the same time, from free memory the card's page cache has left in pieces
  ("Memory budget"). awk alone is above order 5 (and is rarely more than one stage of a
  pipe). grep with sed was order 6 too (the spike's `bbgrep`, 135,440 B): they are two.
- **noMMU's hush executes busybox again for a pipe's builtins, `{ }` and `$( )`**: without fork
  a subshell is a new exec of `/proc/self/exe`, the initramfs' busybox, order 6. So `{ echo b;
  echo a; } | sort` needs busybox's block beside sort's, and `grep x F | sort` only the tools'.
- **The spike's numbers** (2026-11-15, the emulator at 18 MHz; each a command that runs the
  tool once, its first run reading it from the card at ~36 KB/s, the second from the page
  cache; the board's first is ~3 times as slow, "Speed"):

  | Busybox | Size | Exec's block (BSS End + stack) | First | Second |
  |---|---|---|---|---|
  | bbedit | 87,144 B | 112,544 B, order 5 | 4.6 s | 1.7 s |
  | bbgrep with sed | 110,096 B | 135,440 B, order 6 | 4.4 s | 1.4 s |
  | bbsort | 58,360 B | 83,696 B, order 5 | 2.8 s | 1.2 s |
  | bbawk | 133,172 B | 157,664 B, order 6 | 5.0 s | 1.4 s |
  | bbcalc | 102,884 B | 128,176 B, order 5 | 3.4 s | 0.5 s |
  | bbfiles | 91,520 B | 116,880 B, order 5 | 3.5 s | 0.8 s |
  | all in one | 232,980 B | order 6 | 8.1 s | 2.0 s |

  Split (2026-11-15's build): bbgrep 96,332 B (block 122,704 B), bbsed 102,788 B (129,168 B);
  bbsort, without regex, is ~60 KB smaller than either, which is uClibc's regex (~40 KB) and
  the applet. One busybox with all of them would be order 6 for every stage of a pipe, and
  slower to start.
- **A fragment names every feature it wants**: bbconfig.sh turns off what no fragment names,
  default y or not (the first build's awk had no math functions: `Math support is not compiled
  in`). The fragments have their applets' useful defaults back, except regex where it isn't
  already linked (vi's and less's search: ~40 KB, order 6 for bbedit) and anything in bbsed
  and bbcalc, whose blocks are ~2 KB short of order 6 (expr's 64-bit numbers, dc's power and
  exp). Each fragment says what it leaves out.
- `linux_checks.TOOL_COMMANDS` (in `COMMANDS`: the emulator's `test_linux.py` and the board's
  `hw_linux.py`) runs each one on two files the shell writes into `/mnt/sd/TOOLS` (which
  `CARD_CLEANUP` removes), one command at a time; `PIPE_COMMANDS` then two pipes, one with
  `{ }`, which need sysmon's reclaim of the page cache after the card's reads ("Memory
  budget"; `test_linux.py`'s `test_pipe_command`). Each test
  there waits first (`wait_large`, up to 10 s) for a free block of order 6, which sysmon gives
  back within 3 intervals of a read: a person typing leaves it that time. `hw_linux.py` waits
  the same way (`linux_checks.RECLAIM_ORDER`, `RECLAIM_WAIT_S`) before these, the scripts'
  commands, and vi's and cat's execs. It times each busybox's first exec (from the card) by
  the first of `TOOL_COMMANDS` that runs one of its applets (`linux_checks.TOOL_BUSYBOXES`, the
  table above, which `test_linux_checks.py` holds to the fragments and the `.mk`): `[TIME]
  first exec of ...` lines.
- The memory table has a row for them (`sizes` in make linux-dist's `MANIFEST.json`, each by
  its name): they are RAM only while one runs.
- **The terminal**: `/etc/profile` (`board/post-build.sh`) exports `HOME=/mnt/sd` (the card:
  the initramfs is RAM, lost at a reset), `EDITOR=vi` and `TERM=vt100`, and doesn't cd: the
  prompt stays `/ # `, which `linux_checks.PROMPT` and `hw_linux.py` wait for
  (`linux_checks.BASE_COMMANDS` checks the values). The console has no size (TIOCGWINSZ is 0x0
  on a serial port), so vi asks the terminal (`ESC [ 6 n`, `FEATURE_VI_ASK_TERMINAL`) and waits
  100 ms for its answer; a key typed before its screen is drawn is lost, and with no answer
  (the emulator) it is 80x24. vi turns ONLCR off and addresses its cursor (`ESC [ row ; col
  H`), never a bare LF, so `vux_tool.py monitor`'s LF to CR LF leaves its screen alone; Enter
  is CR in its raw mode, which it stores as LF: its files have LF lines.
- **ESC and the arrow keys**: busybox's `read_key` (libbb/read_key.c) takes an ESC alone when
  the next byte doesn't come within 50 ms (one poll per byte). The driver delivers bytes every
  10 ms (polled liteuart, HZ 100), so `ESC [ A` sent back to back, or split 20 ms apart (the
  monitor's pacing at a chunk's end), is the arrow, and an ESC with nothing after it for longer
  is ESC. `linux_checks.VI_SESSIONS` (keys and what the file holds after them; `test_linux.py`'s
  `test_vi`, then `test_card_after_linux` reads the file from the card image: LF, fsck) sends
  each step back to back after the console has been quiet for 300 ms: a new file, an edit
  (`/` search, `dd`, `u`), and the arrow back to back, split, and ESC alone before `[A`.
  `hw_linux.py` runs the same sessions on the board (its `vi`, before `CARD_CLEANUP`, which
  removes the file), each step one write. Through the board's USB bridge only the host's gap
  between a split step's parts is known, not the gap on the wire: a part the bridge held past
  50 ms makes vi read ESC alone, and the `keys` session's text shows it (the failure says so
  when the back-to-back sessions passed). Against the emulator's CLI (`--speed 0`, faster than
  the board) the host's 20 ms is longer than the escape wait, so there it sends a split step
  in one write.
- **Sample scripts** (`linux_workspace/userland/scripts/`, package `vux9k-scripts`, in
  `VUX9K/LINUX/BIN/`): `meminfo` (`/proc/meminfo`'s main lines, `/proc/buddyinfo`'s free blocks
  and the largest, with awk) and `ledctl` (each LED's trigger, `ledctl LED TRIGGER`, `ledctl
  cpu|mem|load` stops the running sysmon, waits for it to exit, which frees the button's line,
  and starts one in that mode; `ledctl off`). `#!/bin/sh`: the kernel's `BINFMT_SCRIPT`
  executes the initramfs' busybox (order 6) for them. meminfo `exec`s awk, which frees the
  shell's block before bbawk's (binfmt_flat's `begin_new_exec` comes before its allocation): a
  shell and awk at once would need two order-6 blocks. This hush has no functions, no `$( )`,
  no `$(( ))` and no sleep, so ledctl is builtins and `${var#pattern}`; a `ps` or `sleep` would
  be a second busybox beside the script's. vfat keeps no modes: init mounts the card without
  options, so fmask is init's umask (0022) and every file is 0755, executable (`cat
  /proc/mounts` shows `fmask=0022`). The names are 8.3 without an extension (the `.mk` stops at
  another); `make_linux_dist.py` refuses a script with CR LF lines (its `#!` line would name
  `/bin/sh` and a CR). `linux_checks.SCRIPT_COMMANDS` runs them (`test_script_command`;
  in `COMMANDS`, so `hw_linux.py` too; `test_ledctl_modes` the modes, whose sysmon lines come
  after the prompt: the emulator's only).
- **Typing at the shell**: `hw_linux.py`'s command lines go 8 bytes at a time, each chunk once
  the shell has echoed the line up to it (`send_line`): the host's clock doesn't say when its
  bytes reach the UART. The emulator's CLI, descheduled on a busy CI host, once handed a
  probe's queued chunks to the UART at once and lost its echo (`test_linux.py`'s
  `test_board_script_with_emulator_stalls` stops the emulator while a line is typed). vi's
  keys, the paste and the lines after it go by the host's clock. A kernel message can land in
  the middle of an echo or a reply (random's `crng init done` at ~255 s of guest time, where the
  emulator's run of the script is at its commands), so the script looks for echoes, prompts and
  replies without lines of printk's `[ seconds.micro] ` form (`linux_checks.KERNEL_LINE`) and
  prints those lines apart. A chunk whose echo doesn't come within `ECHO_TIMEOUT` fails that
  command alone: `run_command` drops the line with ^C and the run goes on to the next.
- **Pasting**: the UART's RX FIFO holds 32 bytes and the polled driver empties it every 10 ms,
  in which 115 bytes can arrive at 115200 bps: a paste sent back to back loses bytes. That is
  the SoC, not a bug, so nothing to pin as xfail: `test_paste` sends 208 bytes
  (`linux_checks.PASTE_LINES`) back to back and wants some dropped (`Soc.uart_rx_dropped` >
  0, the control that shows the paste can lose bytes at all; the emulator is deterministic),
  records how many and how many lines ran, printed by `test_record_measurements`, and asserts
  none lost as `vux_tool.py monitor` sends them (its `Pacer`: 16 bytes, 20 ms apart,
  `--paste-chunk`, `--paste-gap-ms`). It is the last test on a boot: `send_paced` refuses a
  SoC that has dropped a byte. `hw_linux.py` does the same last, after the card is unmounted
  (`[PASTE]` lines: the lines that came through, as the board has no counter of dropped bytes;
  the paced paste must pass all of them, the one back to back is only recorded). A lost LF can
  leave a line open, so it types an empty line before `PASTE_DONE`.
  Measured (2026-11-15, 63cd76b): back to back, the emulator dropped 171-176 of the 208 bytes
  and 1 of 8 lines came through, the board 2 of 8; paced, 8 of 8 on both, and on the board the
  `keys` session's arrow split 20 ms apart still read as one key through the USB bridge.

## Speed (2026-11-01, emulator; 2026-11-11, 2026-11-14 and 2026-11-15, the board)

With the SD card (2026-11-14, the board, 1bc6b3b's kernel, a 128 GB SDXC card): `linux` to
`[LINUX]` (the 3.7 MB Image) 14.9 s, `[LINUX]` to the prompt 12.7 s (the emulator: 10.6 s; init
waits for the real card's partition longer), the bitstream's load to the prompt 28.6 s. On the
card: a 256 KB `dd` 15.1 s (the emulator 14.0 s), a write and `sync` 1.4 s, but the first exec
of memprobe from it 9.6 s and the first `ls /mnt/sd/VUX9K` 3.2 s (the emulator 3.3 s and 0.9 s).
patches/0004-mmc-spi-token-search.patch (16 bytes per message while it searches for the data
token, "The SD card") halves a read-heavy command on the emulator's card with read waits, but
on the board it changed little (2026-11-15, 63cd76b, the same card): memprobe's first exec
8.4 s, the first `ls /mnt/sd/VUX9K` 3.4 s, the 256 KB `dd` 14.1 s. So this card doesn't make
mmc_spi wait long for its tokens, and why its small reads are slower than the emulator's is
still open. With 0004, the bitstream's load to the prompt 30.3 s (`linux` to `[LINUX]` 14.6 s,
`[LINUX]` to the prompt 14.8 s); each card busybox's first exec 3.1-5.0 s (bbcalc 3.1, bbsort
3.4, bbsed 3.6, bbedit 3.8, bbfiles 4.4, bbawk 4.7, bbgrep 5.0); the two pipes 2.6 s (with `{ }`)
and 1.8 s; vi's screen 1.0 s. `hw_linux.py`'s `[TIME]` lines give these: each command, each of
the card's busyboxes' first exec, vi's sessions.

On the board (`make test-hw-linux-dist`, 1c8d685, three runs): the second stage's `linux` to
its `[LINUX]` line (reading the 3.2 MB Image) 13.1 s, `[LINUX]` to busybox's prompt
11.9-12.5 s, the bitstream's load to that prompt 25.7-26.3 s; `uname -a` 0.7 s, `cat
/proc/cpuinfo` 0.9 s. The emulator's cycles predict these within a few percent (12.8 s and
12.0 s). A `put` of the Image takes ~350 s (~9.3 KB/s).


The boot to the prompt is ~224M cycles (12.5 s at 18 MHz; from the second stage's `[LINUX]`
line ~214M, after its ~230M-cycle load), with CPI 9.7, the I cache hitting
92.8% and the D cache 88.4%. A command that executes busybox is 13-15M cycles (~0.8 s). The
measurements are in `test_linux.py`'s output (`make test-linux-emu`). Before the 8-word lines and
the write-back data cache the same boot was ~3.1G cycles (173 s, CPI 25): its lines were one word,
so straight-line code missed every word at 35 cycles, stores each waited 34, and the timer tick's
work took most of the CPU.
