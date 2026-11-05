---
paths:
  - "linux_workspace/**"
  - "sim/emu/test_linux.py"
  - "emu/crates/vux9k_emu/src/profile.rs"
  - "emu/crates/vux9k_emu/src/csr.rs"
  - "emu/crates/vux9k_emu/src/periph/clint.rs"
  - "emu/crates/vux9k_emu/src/periph/uart.rs"
  - "emu/crates/vux9k_emu/tests/linux.rs"
---

# noMMU Linux (linux_workspace/, the emulator's Linux profile)

Read before changing `linux_workspace/`, `make linux-src`, `linux-rootfs`, `linux` or the
emulator's Linux profile. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

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
  `linux_workspace/dts/vux9k-linux-emu.dts` (`vux9k-linux-emu.dtb`).
- `make test-linux-emu` (`sim/emu/test_linux.py`) boots it on the emulator's Linux profile to
  busybox's shell and runs a few commands. It runs only with `VUX9K_LINUX_TEST=1`, which that
  target sets, so `test-emu` (`pytest sim/emu`, CI) skips it even where an Image exists. None of
  these targets is in CI. The first buildroot run takes ~40 min on a 2-vCPU
  machine, a kernel rebuild ~6 min.
- **The profile is the real SoC.** Its UART's liteuart registers (`periph/uart.rs`) are the
  RTL's (`uart_regs`) and `Real`'s, held to the RTL by the lockstep program `liteuart`, as its
  CLINT at `0x2000_0000` (`periph/clint.rs`, `timer_core`) is by `clint`; the profile differs
  from `Real` only in letting `load_linux` start a kernel. Nothing here has run on the board
  yet, and no document may say it does.

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
  once the kernel runs. The DTB sits in the top 64 KB (`soc::LINUX_DTB_ADDR`, `0x807F_0000`),
  and the kernel reserves its pages.
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

## Speed (2026-11-01, emulator)

The boot to the prompt is ~224M cycles (12.5 s at 18 MHz), with CPI 9.7, the I cache hitting
92.8% and the D cache 88.4%. A command that executes busybox is 13-15M cycles (~0.8 s). The
measurements are in `test_linux.py`'s output (`make test-linux-emu`). Before the 8-word lines and
the write-back data cache the same boot was ~3.1G cycles (173 s, CPI 25): its lines were one word,
so straight-line code missed every word at 35 cycles, stores each waited 34, and the timer tick's
work took most of the CPU.
