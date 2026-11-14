#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT
#
# flthdr.sh FLTHDR OUT TOOL:ORDER...: for each of the card's busyboxes (OUT/<tool>/busybox, built
# by this directory's Makefile), its size, applets and `flthdr -p`, and the block its exec
# allocates, then fails if one's block is above the ORDER given for it. binfmt_flat copies text
# and data and leaves room for max(bss + stack, relocations) in one block of a power of two
# pages: Data End + max(BSS End - Data End + the stack, 4 x Reloc Count), plus ARGS for the
# arguments' and the environment's strings and pointers, which go on that stack too. The
# limits are docs/agents/linux.md's ("Text tools and vi"): the processes of a pipe each need
# one such block at once, from free memory the card's page cache has left in pieces. FLTHDR
# is the toolchain's flthdr; the table goes to stdout (images/bbtools-flthdr.txt), what fails
# to stderr too.
set -eu
flthdr=$1
out=$2
shift 2
page=4096
# A login shell's environment and a command line of a few hundred bytes, rounded up
args=1024
over=
# A field of $hdr (flthdr -p of $f), in decimal
field() {
	v=$(echo "$hdr" | sed -n "s/^ *$1: *\(0x[0-9a-fA-F]*\).*/\1/p")
	[ -n "$v" ] || { echo "flthdr.sh: no $1 in flthdr -p $f" >&2; exit 1; }
	printf '%d' "$v"
}
for spec in "$@"; do
	t=${spec%%:*}
	max=${spec#*:}
	case $max in
	'' | *[!0-9]*)
		echo "flthdr.sh: $spec: not TOOL:ORDER" >&2
		exit 1
		;;
	esac
	f=$out/$t/busybox
	hdr=$("$flthdr" -p "$f")
	data_end=$(field 'Data End')
	bss_end=$(field 'BSS End')
	stack=$(field 'Stack Size')
	relocs=$(field 'Reloc Count')
	extra=$((bss_end - data_end + stack))
	[ "$extra" -ge $((relocs * 4)) ] || extra=$((relocs * 4))
	bytes=$((data_end + extra + args))
	pages=$(((bytes + page - 1) / page))
	order=0
	while [ $((1 << order)) -lt "$pages" ]; do order=$((order + 1)); done
	mark=
	if [ "$order" -gt "$max" ]; then
		mark=" OVER ITS ORDER $max"
		over="$over $t (order $order, at most $max)"
	fi
	echo "== $t: $(wc -c <"$f") B; applets: $(sed 's|.*/||' "$out/$t/busybox.links" | tr '\n' ' ')"
	echo "$hdr"
	echo "    exec's block: $bytes B, $pages pages, order $order (at most $max)$mark"
done
if [ -n "$over" ]; then
	echo "== over their order:$over"
	echo "flthdr.sh: a busybox's exec needs a block above its order:$over (docs/agents/linux.md, \"Text tools and vi\")" >&2
	exit 1
fi
echo "== every one at its order or below"
