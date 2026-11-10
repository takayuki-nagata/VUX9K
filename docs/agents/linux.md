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
  `build/linux-dist/` (`VUX9K/LINUX/Image` and `vux9k.dtb`, the kernel's `linux.config`,
  `MANIFEST.json` with each card file's size and sha256, `SHA256SUMS`). It is not part of
  `make dist`, which needs no Linux build. CI's job `linux` (beside `test`, so the kernel adds
  nothing to that job's time) runs `make linux-dist test-linux-emu` and on pushes uploads the
  tree as `vux9k-linux-<sha>`. It caches `build/buildroot` under an exact key (the source pins
  and `linux_workspace/buildroot/`), never a partial match: buildroot would keep the packages
  an older configuration built (below).
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
  plus init, hush and core applets, ~330 KB), and `board/post-build.sh` writes a two-line
  inittab. Buildroot's `busybox-minimal.config` (780 KB) needed order-8 allocations that 8 MB
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

## Speed (2026-11-01, emulator; 2026-11-11, the board)

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
