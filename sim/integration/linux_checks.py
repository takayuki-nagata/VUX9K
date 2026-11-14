# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

"""What make linux's kernel must print on the console, shared by its emulator test
(sim/emu/test_linux.py) and its board test (scripts/hw_linux.py): the lines of its boot to
busybox's shell, and commands typed at the shell with what each must answer (one of them a
builtin, the others busybox or this tree's programs executed: FLAT, copied into RAM at each
exec)."""

PROMPT = "/ # "
# The system monitor init starts (linux_workspace/userland/init/), its name in ps
SYSMON = "sysmon"
SYSMON_PATH = "/usr/sbin/sysmon"
# A file the commands write on the card
SD_TEST_FILE = "/mnt/sd/VUX9K/SDTEST.TXT"
BOOT_LINES = (
    "Linux version 7.3",
    "clint: clint@20000000: timer running at 18000000 Hz",
    "printk: legacy console [liteuart0] enabled",
    "Run /init as init process",
    # This tree's init (linux_workspace/userland/init/)
    "init: /dev/mmcblk0p1 mounted on /mnt/sd",
    "hush - the humble shell",
)
# command -> what its output must contain: the shell and the card (BASE_COMMANDS), then the
# LEDs and sysmon (LED_COMMANDS), which sim/emu/test_linux.py runs with its own tests between
BASE_COMMANDS = {
    "echo hello-vux9k": ("hello-vux9k",),
    "uname -a": ("Linux (none) 7.3.0",),
    "ls /": ("bin", "dev", "proc", "sbin"),
    "cat /proc/cpuinfo": ("rv32ima",),
    # The SD card (mmc_spi on spi-vux9k-sd) and its MBR's FAT32 partition, which init mounts
    # (linux_workspace/userland/init/)
    "cat /proc/partitions": ("mmcblk0", "mmcblk0p1"),
    "cat /proc/mounts": ("/dev/mmcblk0p1 /mnt/sd vfat ",),
    # The card's programs (make linux-dist's VUX9K/LINUX/BIN/) are on the login shell's PATH,
    # after the initramfs': this tree's own program memprobe (linux_workspace/userland/memprobe/:
    # the largest free block allocated, with a copy of itself in RAM), on the card only, by its
    # name and its path
    "echo $PATH": ("/mnt/sd/VUX9K/LINUX/BIN",),
    "memprobe": ("memprobe: MemFree ", "), allocated"),
    "ls /mnt/sd/VUX9K": ("LINUX", "STAGE2.BIN"),
    "/mnt/sd/VUX9K/LINUX/BIN/memprobe": ("memprobe: MemFree ", "), allocated"),
    # A write to the card, read back by another command (the first's echo holds its text)
    f"echo sd-write-ok > {SD_TEST_FILE}; sync": (),
    f"cat {SD_TEST_FILE}": ("sd-write-ok",),
    # The card's read speed (the command's time): 256 KB from the start of the card
    "dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=64": ("64+0 records in",),
}
LED_COMMANDS = {
    # The board's LEDs in the LED class (the device tree's gpio-leds on upstream's gpio-mmio),
    # led0 and led5 with the kernel's triggers, led1-led4 sysmon's
    "ls /sys/class/leds": tuple(f"led{n}" for n in range(6)),
    "cat /sys/class/leds/led0/trigger": ("[heartbeat]",),
    "cat /sys/class/leds/led5/trigger": ("[mmc0]",),
    # The system monitor (linux_workspace/userland/sysmon/), which init started at boot, and a
    # second one for two intervals: the first holds the button's line, so this one has none
    "ps": (SYSMON,),
    "sysmon -m mem -i 200 -n 2 -v": (
        "sysmon: no button",
        "sysmon: mode mem",
        "sysmon: mem ",
        "% bar ",
        "sysmon: stopped",
    ),
}
COMMANDS = BASE_COMMANDS | LED_COMMANDS
# What the board's test does to its card after COMMANDS: the file removed and the card
# unmounted, so the FAT's dirty bit is clear when the bitstream is loaded again (the emulator's
# card is thrown away: sim/emu/test_linux.py checks it with the first and second stage after an
# umount)
CARD_CLEANUP = (f"rm {SD_TEST_FILE}; sync", "umount /mnt/sd")
# The mount table, read with the shell's builtins (umount's own exec can fail with only the
# kernel's report on the console); the card is unmounted when its output lacks CARD_MOUNTED
MOUNTS_PROBE = "exec 3</proc/mounts; while read l <&3; do echo $l; done; exec 3<&-"
CARD_MOUNTED = " /mnt/sd "


def missing(out: str, wanted) -> list[str]:
    """What of wanted is not in out."""
    return [w for w in wanted if w not in out]


def pids(ps_out: str, name: str) -> list[int]:
    """The processes busybox's ps lists with that command, its arguments aside. This busybox's
    ps prints `  PID USER       VSZ STAT COMMAND` lines without that header (2026-11)."""
    found = []
    for line in ps_out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].isdigit() and (f[4] == name or f[4].endswith("/" + name)):
            found.append(int(f[0]))
    return found


def cpu_ticks(stat: str) -> int:
    """A process's CPU time so far in clock ticks (utime + stime) from its /proc/<pid>/stat."""
    # The command (field 2) is in parentheses and may hold spaces: count from its end
    f = stat[stat.rindex(")") + 2 :].split()
    return int(f[11]) + int(f[12])


# The memory the userland has, read at the shell with its builtins only (hush's `exec`, `read`
# and `echo`): no exec of a program, so no copy of busybox in RAM while it is measured (noMMU,
# FLAT: every exec copies the whole executable into contiguous pages, docs/agents/linux.md,
# "Memory budget"). The file is opened on fd 3 and each `read` redirected from it: this hush
# drops a `while ...; done < FILE` redirection after the loop's first pass, and the loop then
# reads the console
MEM_PROBES = tuple(
    f"exec 3<{path}; while read l <&3; do echo $l; done; exec 3<&-" for path in ("/proc/meminfo", "/proc/buddyinfo")
)
PAGE_KB = 4
# What the table holds, in its order; the sizes come from make linux-dist's MANIFEST.json
TABLE_ROWS = (
    "Image",
    "initramfs (rootfs.cpio)",
    "busybox",
    "MemTotal",
    "MemFree",
    "largest free block",
    "free blocks of order >= 6",
)


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo's `Name: N kB` lines as {Name: N}; other lines (the command's echo) are
    skipped."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0].endswith(":") and parts[1].isdigit() and parts[2] == "kB":
            out[parts[0][:-1]] = int(parts[1])
    return out


def parse_buddyinfo(text: str) -> list[int]:
    """/proc/buddyinfo's free blocks per order (index = order), summed over its zones; its
    lines are `Node 0, zone Normal n0 n1 ...`."""
    counts: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "Node" or parts[2] != "zone" or not all(p.isdigit() for p in parts[4:]):
            continue
        for order, n in enumerate(int(p) for p in parts[4:]):
            if order == len(counts):
                counts.append(0)
            counts[order] += n
    return counts


def memory(meminfo: str, buddyinfo: str, sizes: dict[str, int] | None = None) -> dict[str, str]:
    """TABLE_ROWS' values from the probes' output and the build's sizes (bytes), as text;
    raises ValueError when the probes' output lacks what the table needs."""
    info = parse_meminfo(meminfo)
    orders = parse_buddyinfo(buddyinfo)
    lacking = [k for k in ("MemTotal", "MemFree") if k not in info] or ([] if orders else ["buddyinfo"])
    if lacking:
        raise ValueError(f"the probes' output lacks {', '.join(lacking)}")
    largest = max((o for o, n in enumerate(orders) if n), default=None)
    rows = {}
    for name, key in (("Image", "image"), ("initramfs (rootfs.cpio)", "rootfs_cpio"), ("busybox", "busybox")):
        if sizes and key in sizes:
            rows[name] = f"{sizes[key]:,} B"
    rows["MemTotal"] = f"{info['MemTotal']:,} kB"
    rows["MemFree"] = f"{info['MemFree']:,} kB"
    rows["largest free block"] = "none" if largest is None else f"{PAGE_KB << largest:,} kB (order {largest})"
    rows["free blocks of order >= 6"] = " ".join(f"{o}:{n}" for o, n in enumerate(orders) if o >= 6)
    return rows
