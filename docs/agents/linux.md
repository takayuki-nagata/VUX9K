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
- **Reading the card from Linux is ~500 cycles a byte** (~36 KB/s at 18 MHz, the emulator,
  2026-11; the second stage reads at ~100): a profile of a `dd` from the card put ~27% in the
  driver's byte loop (48 cycles a byte are the wire at 3 MHz), ~16% in the SPI core (mmc_spi
  sends several one-byte messages per block, e.g. its token search), ~6% in the CRC16 and ~32%
  in the timer tick and the scheduler, which take ~23% of the CPU even when it is idle (one HZ
  100 tick is ~40k cycles here). The block device's readahead (128 KB) also reads past what a
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
  interval (`sysmon: cpu 37% bar 2`). It writes an LED's `brightness` when it changes, and all
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
(`memory`): the Image's, the initramfs' (`rootfs.cpio`) and busybox's sizes (make linux-dist's
`MANIFEST.json`, `sizes`), MemTotal and MemFree, the largest free block with its order, and the
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

## Speed (2026-11-01, emulator; 2026-11-11 and 2026-11-14, the board)

With the SD card (2026-11-14, the board, 1bc6b3b's kernel, a 128 GB SDXC card): `linux` to
`[LINUX]` (the 3.7 MB Image) 14.9 s, `[LINUX]` to the prompt 12.7 s (the emulator: 10.6 s; init
waits for the real card's partition longer), the bitstream's load to the prompt 28.6 s. On the
card: a 256 KB `dd` 15.1 s (the emulator 14.0 s), a write and `sync` 1.4 s, but the first exec
of memprobe from it 9.6 s and the first `ls /mnt/sd/VUX9K` 3.2 s (the emulator 3.3 s and 0.9 s):
a real card answers a read only after a wait (N(AC) bytes of FF), and mmc_spi searches for the
data token one byte per SPI message, so small reads (FAT, directories, a cold exec) pay for
the SPI core's per-message cost far more than on the emulator's card, which answers at once.

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
