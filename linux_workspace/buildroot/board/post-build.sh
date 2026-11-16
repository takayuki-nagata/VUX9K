#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# buildroot's post-build script, $1 the target directory. The initramfs' /init is this tree's
# (package vux9k-init: /dev, /proc, the SD card on /mnt/sd, the login shell on the console), so
# no inittab: busybox init doesn't run. Its earlier files go, and memprobe's copy in the
# initramfs from before it went to the card only, as buildroot keeps a target directory between
# builds.
set -eu
rm -f "$1/etc/inittab" "$1/etc/init.d/sdcard" "$1/usr/bin/memprobe"
mkdir -p "$1/mnt/sd" "$1/run"

# The login shell's (-/bin/sh) profile: the card's programs (make linux-dist's VUX9K/LINUX/BIN/)
# after the initramfs' own; HOME on the card (vfat, where files stay; the initramfs is RAM and
# lost at a reset), but no cd: the prompt stays "/ # ", which the tests wait for; vi as the
# editor; TERM what busybox's vi and less send (VT100's escape sequences, no terminfo here);
# MPY's modules where the card's pylib extracts them (MPY's own default too, for a program
# not started from this shell: the variant's MICROPY_PY_SYS_PATH_DEFAULT). Then pylib from the
# card, sourced (no exec of busybox for it): VUX9K/LINUX/PYLIB.TAR extracted when it changed
# (docs/agents/linux.md, "MicroPython"); once a boot, by pylib's /run/pylib.ver in the
# initramfs, as init starts the shell again whenever it exits
cat > "$1/etc/profile" <<'SH'
export PATH=/sbin:/usr/sbin:/bin:/usr/bin:/mnt/sd/VUX9K/LINUX/BIN
export HOME=/mnt/sd EDITOR=vi TERM=vt100
export MICROPYPATH=/mnt/sd/VUX9K/LINUX/PYLIB
if [ ! -e /run/pylib.ver ] && [ -f /mnt/sd/VUX9K/LINUX/BIN/pylib ]; then
	. /mnt/sd/VUX9K/LINUX/BIN/pylib
fi
SH
