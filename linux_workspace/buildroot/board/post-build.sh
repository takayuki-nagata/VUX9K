#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# buildroot's post-build script, $1 the target directory. busybox init's table, in place of the
# skeleton's: every line there is a process, and each exec copies busybox into contiguous RAM
# (noMMU, FLAT). /proc, the SD card's FAT32 partition on /mnt/sd, then the shell on the console
# (the UART), started again when it exits.
set -eu
cat > "$1/etc/inittab" <<'TAB'
::sysinit:/bin/mount -t proc proc /proc
::sysinit:/bin/sh /etc/init.d/sdcard
console::respawn:-/bin/sh
TAB

# mmc finds the card in the background (its rescan work), possibly after init has started: wait
# for the partition up to 5 s (hush's builtin test; this hush has no arithmetic), then mount
# it. No card is not an error
mkdir -p "$1/mnt/sd" "$1/etc/init.d"
cat > "$1/etc/init.d/sdcard" <<'SH'
for n in 1 2 3 4 5; do
	[ -b /dev/mmcblk0p1 ] && break
	sleep 1
done
if [ -b /dev/mmcblk0p1 ]; then
	mount -t vfat /dev/mmcblk0p1 /mnt/sd || echo "sdcard: /dev/mmcblk0p1 not mounted"
else
	echo "sdcard: no card"
fi
SH

# The login shell's (-/bin/sh) profile: the card's programs (make linux-dist's VUX9K/LINUX/BIN/)
# after the initramfs' own
cat > "$1/etc/profile" <<'SH'
export PATH=/sbin:/usr/sbin:/bin:/usr/bin:/mnt/sd/VUX9K/LINUX/BIN
SH
