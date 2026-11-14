# Copyright (c) 2026 Takayuki Nagata
# SPDX-License-Identifier: MIT

# The card's busyboxes, one for each use, each from common.config and its own fragment
# (bbconfig.sh), built out of the busybox tree BBTOOLS_SRC into BBTOOLS_OUT/<name>/ (busybox's
# O=). Run by buildroot's package vux9k-bbtools (linux_workspace/buildroot/package/), which
# passes what buildroot's busybox package does (CC, CROSS_COMPILE, EXTRA_LDFLAGS, ... on the
# command line, so they reach busybox's make too; CFLAGS in the environment). Each run writes the
# .config again: busybox's kbuild then rebuilds what a changed fragment touches.
# BBTOOLS_NAMES, the busyboxes to build, is the package's list (VUX9K_BBTOOLS_TOOLS, with each
# one's order: flthdr.sh); each name is the file's on the card, 8 characters at most (8.3, no
# extension), and <name>.config its fragment here.

all: $(BBTOOLS_NAMES)
	@test -n "$(BBTOOLS_NAMES)" || { echo "BBTOOLS_NAMES is needed"; exit 1; }

$(BBTOOLS_NAMES): FORCE
	@test -n "$(BBTOOLS_SRC)" -a -n "$(BBTOOLS_OUT)" || { echo "BBTOOLS_SRC and BBTOOLS_OUT are needed"; exit 1; }
	@test "$$(printf %s $@ | wc -c)" -le 8 || { echo "$@: longer than the card's 8.3 names"; exit 1; }
	MAKE="$(MAKE)" sh $(CURDIR)/bbconfig.sh $(BBTOOLS_SRC) $(BBTOOLS_OUT)/$@ \
		$(CURDIR)/common.config $(CURDIR)/$@.config
	$(MAKE) -C $(BBTOOLS_SRC) O=$(BBTOOLS_OUT)/$@ busybox busybox.links

FORCE:

# One busybox at a time (each build is parallel): they share the source tree
.NOTPARALLEL:
.PHONY: all FORCE
