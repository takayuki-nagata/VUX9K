#!/bin/sh
# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT
#
# bbconfig.sh SRC OUT FRAGMENT...: the .config in OUT (busybox's O=) that has the fragments'
# options and nothing else: allnoconfig, the fragments' lines over it (CONFIG_X=..., and
# "# CONFIG_X is not set" for a choice's default that allnoconfig left on), then
# oldconfig, with every option it adds as y that no fragment names turned off again, until it
# adds none. busybox's kconfig resets every bool in allnoconfig, KCONFIG_ALLCONFIG's too, and
# oldconfig alone would take the defaults (mostly y) of the options an enabled applet brings in.
# Stops if a fragment's line is not in the result (a misspelt option, or one whose dependencies
# are off). $MAKE is the make to run busybox's kconfig with.
set -eu
src=$1
out=$2
shift 2
mkdir -p "$out"
cfg=$out/.config
frag=$out/fragment.config

kconfig() {
	${MAKE:-make} -s -C "$src" O="$out" "$1" </dev/null >>"$out/kconfig.log" 2>&1 ||
		{ tail -20 "$out/kconfig.log" >&2; exit 1; }
}
# The options a .config names, set or not
names() {
	sed -n -e 's/^\(CONFIG_[A-Za-z0-9_]*\)=.*/\1/p' \
		-e 's/^# \(CONFIG_[A-Za-z0-9_]*\) is not set$/\1/p' "$1" | sort -u
}

: >"$out/kconfig.log"
cat "$@" | grep -E '^CONFIG_[A-Za-z0-9_]+=|^# CONFIG_[A-Za-z0-9_]+ is not set$' >"$frag"
kconfig allnoconfig
while IFS= read -r line; do
	sym=${line#\# }
	sym=${sym%%[= ]*}
	sed -i -e "/^$sym=/d" -e "/^# $sym is not set\$/d" "$cfg"
	echo "$line" >>"$cfg"
done <"$frag"

# Each pass can bring in the options of the ones the last pass left on; a few levels at most
pass=0
while :; do
	names "$cfg" >"$out/names.before"
	kconfig oldconfig
	names "$cfg" | comm -13 "$out/names.before" - >"$out/names.new"
	[ -s "$out/names.new" ] || break
	pass=$((pass + 1))
	if [ "$pass" -gt 10 ]; then
		echo "bbconfig.sh: $out/.config still changing after 10 passes" >&2
		exit 1
	fi
	while IFS= read -r sym; do
		if grep -qx "$sym=y" "$cfg" && ! grep -qx "$sym=y" "$frag"; then
			sed -i "s/^$sym=y\$/# $sym is not set/" "$cfg"
		fi
	done <"$out/names.new"
done

missing=0
while IFS= read -r line; do
	case $line in
	"# "*)
		sym=${line#\# }
		sym=${sym%% *}
		! grep -q "^$sym=y\$" "$cfg" || { echo "bbconfig.sh: set in $cfg: $sym" >&2; missing=1; }
		;;
	*) grep -qxF "$line" "$cfg" || { echo "bbconfig.sh: not in $cfg: $line" >&2; missing=1; } ;;
	esac
done <"$frag"
exit "$missing"
