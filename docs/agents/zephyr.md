---
paths:
  - "zephyr_workspace/**"
---

# Zephyr

Read before changing `zephyr_workspace/`. Index and the rules that apply everywhere: [`AGENTS.md`](../../AGENTS.md).

## Zephyr: two boards, and what runs on which

`zephyr_workspace/boards/vux9k/` defines two HWMv2 targets sharing `vux9k-common.dtsi`
(18 MHz, `vux9k,uart`, and the SoC timer as `andestech,machine-timer`, whose
mtime +0x0 / mtimecmp +0x8 layout is `timer_core`'s, so Zephyr's in-tree
`riscv_machine_timer` driver serves it):
- `vux9k`: the real board. flash = I-RAM 0x0000-0x37FF (the Resident Loader owns
  0x3800+), sram = D-RAM less the mailbox words at 0x1FF8/0x1FFC. The linker enforces
  both, so an app that outgrows the board fails to link.
- `vux9k/vux9k/ext`: 512 KB/256 KB, **emulator only** (`vux9k-emu --profile extended`).
  bc_clone_rs (`make build-zephyr`, ~260 KB) runs only here; never present it as
  running on hardware.

`zephyr_workspace/app/` is the Rust demo: Zephyr (`src/main.c`, FFI wrappers for
`printk`/`k_msleep`/`k_uptime_get_32`) calls `rust_main()` from the `rust_demo`
staticlib. `make build-zephyr-demo` builds it for `vux9k` into `build/zephyr-demo/`
(cargo output included); it is what `scripts/test_hardware.py` flashes to Slot 1, and
it is tested from SD on the emulator (`sim/emu/test_zephyr_demo.py`) and the RTL
(`sim-zephyr-demo-rtl`). Its `prj.conf` size settings are what make it fit in 14 KB.

Two traps found while bringing Zephyr up on the RTL (the old Python emulator hid both):
- **The SoC must select Zifencei.** The Zephyr SDK has no libgcc multilib for
  `rv32i_zicsr`; GCC silently links a default one built with RV32M, and the first
  64-bit division (`__udivdi3` -> `divu`) is an illegal instruction.
- **XIP `.data` must be in the image.** An XIP image's `.data` runs in D-RAM but loads
  from I-RAM; the slot image must carry it at its load address, or Zephyr's startup
  copies zeros into `.data` (e.g. an empty-but-not-self-linked `timeout_list`, so
  `k_msleep` never woke up). Zephyr's own `zephyr.bin` (objcopy by LMA) always did;
  the bug was the Makefile overwriting it with an older `scripts/elf2bin.py` that
  dropped such segments. Use `zephyr.bin` as `west build` writes it (byte-identical to
  today's elf2bin output on both boards, checked 2026-09); elf2bin is for the firmware.
