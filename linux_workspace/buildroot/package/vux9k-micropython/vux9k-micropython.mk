################################################################################
#
# vux9k-micropython
#
################################################################################

# MicroPython's unix port as a FLAT program on the card (VUX9K/LINUX/BIN/MPY), and bc_clone_py's
# modules compiled to .mpy for the card (docs/agents/linux.md, "MicroPython"). Not buildroot's
# micropython package: it needs threads and shared libraries, and this toolchain has neither
# (BR2_PTHREADS_NONE, FLAT). Nothing is frozen into the program: each exec copies all of it
# into one block of contiguous RAM, and the modules are read from the card only when imported.
# The source is MicroPython's release tarball and bc_clone_py's archive, the same pins as the
# Makefile's MICROPY_* and BC_PY_*; the variant (the configuration) is read where it is, in
# linux_workspace/userland/micropython/variant/, outside this external tree (CI's cache key),
# so make linux-rootfs's vux9k-micropython-rebuild (VUX9K_BR_PACKAGES in the Makefile) builds
# what a change there touches (docs/agents/linux.md, "Userland packages").
VUX9K_MICROPYTHON_VERSION = 1.29.0
VUX9K_MICROPYTHON_SOURCE = micropython-$(VUX9K_MICROPYTHON_VERSION).tar.xz
VUX9K_MICROPYTHON_SITE = https://github.com/micropython/micropython/releases/download/v$(VUX9K_MICROPYTHON_VERSION)
VUX9K_MICROPYTHON_LICENSE = MIT
VUX9K_MICROPYTHON_LICENSE_FILES = LICENSE
# bc_clone_py: buildroot saves an extra download under its URL's last component, the commit
# (the name in the .hash), a gzipped tar
VUX9K_MICROPYTHON_BC_COMMIT = 4e5f33959135a8171c35909317e8cab2cc85729b
VUX9K_MICROPYTHON_EXTRA_DOWNLOADS = \
	https://codeload.github.com/takayuki-nagata/bc_clone_py/tar.gz/$(VUX9K_MICROPYTHON_BC_COMMIT)
VUX9K_MICROPYTHON_BC_DIR = $(@D)/vux9k-bc_clone_py
# The modules the card gets, as .mpy (`import bc_selftest; bc_selftest.run()`)
VUX9K_MICROPYTHON_BC_MODULES = bc_clone bc_eval bc_math bc_parser bc_selftest
# Where they are compiled. make linux-dist makes images/pylib/ (INSTALL_IMAGES_CMDS) into the
# card's VUX9K/LINUX/PYLIB.TAR (scripts/make_linux_dist.py, PYLIB_SRC): one archive with an 8.3
# name, as the stages and mkfat write no long names, which the login shell's profile extracts
# with theirs (linux_workspace/userland/scripts/pylib)
VUX9K_MICROPYTHON_PYLIB = $(@D)/vux9k-pylib
VUX9K_MICROPYTHON_VARIANT_DIR = $(abspath $(BR2_EXTERNAL_VUX9K_PATH)/../userland/micropython/variant)
# The port's build directory for a VARIANT_DIR: build-<its last component>
VUX9K_MICROPYTHON_BUILD = $(@D)/ports/unix/build-variant
VUX9K_MICROPYTHON_MPYCROSS = $(@D)/mpy-cross/build/mpy-cross
VUX9K_MICROPYTHON_INSTALL_TARGET = NO
VUX9K_MICROPYTHON_INSTALL_IMAGES = YES
# FLAT's stack is fixed at link time (elf2flt -s): bc_clone_py's parser and evaluator recurse
# on the C stack (the Zephyr build needed 16 KB). The variant's MICROPY_UNIX_STACK_LIMIT
# (patch 0001) stays below it, with room for the arguments and the environment, which the
# kernel puts on the same stack.
VUX9K_MICROPYTHON_FLAT_STACKSIZE = 0x6000

define VUX9K_MICROPYTHON_EXTRACT_BC
	rm -rf $(VUX9K_MICROPYTHON_BC_DIR)
	mkdir -p $(VUX9K_MICROPYTHON_BC_DIR)
	$(ZCAT) $(VUX9K_MICROPYTHON_DL_DIR)/$(VUX9K_MICROPYTHON_BC_COMMIT) | \
		$(TAR) --strip-components=1 -C $(VUX9K_MICROPYTHON_BC_DIR) $(TAR_OPTIONS) -
endef
VUX9K_MICROPYTHON_POST_EXTRACT_HOOKS += VUX9K_MICROPYTHON_EXTRACT_BC

# The banner's version without git: py/makeversionhdr.py would take `git describe` of the
# tree around the build directory, this repository (the Makefile's build-micropython does the
# same); the date is buildroot's SOURCE_DATE_EPOCH
VUX9K_MICROPYTHON_ENV = MICROPY_GIT_TAG=v$(VUX9K_MICROPYTHON_VERSION)

# The port's knobs are the variant's (mpconfigvariant.mk, whose empty FROZEN_MANIFEST freezes
# nothing). CFLAGS_EXTRA and LDFLAGS_EXTRA come after the port's own: buildroot's (-fPIC, and
# elf2flt's -r and -s in LDFLAGS) and -Os again over buildroot's optimization level (the size
# is RAM at each exec). No -Werror (CWARN): uClibc's headers aren't glibc's. STRIP and SIZE run
# on the link's output, which elf2flt has made a FLAT file neither knows (the ELF is
# micropython.gdb); STRIP=true also leaves out the port's -ggdb3.
VUX9K_MICROPYTHON_MAKE_OPTS = \
	VARIANT_DIR=$(VUX9K_MICROPYTHON_VARIANT_DIR) \
	CROSS_COMPILE=$(TARGET_CROSS) \
	CFLAGS_EXTRA="$(TARGET_CFLAGS) -Os" \
	LDFLAGS_EXTRA="$(TARGET_LDFLAGS)" \
	CWARN="-Wall -Wno-unused-parameter" \
	MICROPY_MPYCROSS=$(VUX9K_MICROPYTHON_MPYCROSS) \
	STRIP=true \
	SIZE=true

# mpy-cross for the host first. A variant changed since the last build starts the port's
# build from scratch: its scan for modules re-reads only the sources newer than its output, so
# a module the variant turns off would stay registered. Then the FLAT header (`flthdr -p`: the
# exec's block is text + data + bss + stack) as images/micropython-flthdr.txt, and the modules
# compiled to bytecode. No -march: the program has no native emitter (the variant), so a
# module is bytecode only, which any 32-bit port of the same .mpy version loads. mpy-cross runs
# in the modules' directory, so each .mpy names its source by its file name alone, not by a
# path on this machine
define VUX9K_MICROPYTHON_BUILD_CMDS
	$(HOST_MAKE_ENV) $(VUX9K_MICROPYTHON_ENV) $(MAKE) -C $(@D)/mpy-cross \
		CC="$(HOSTCC)" CROSS_COMPILE=
	if [ ! -f $(VUX9K_MICROPYTHON_BUILD)/.vux9k-variant ] || \
		[ -n "`find $(VUX9K_MICROPYTHON_VARIANT_DIR) -newer $(VUX9K_MICROPYTHON_BUILD)/.vux9k-variant`" ]; then \
		rm -rf $(VUX9K_MICROPYTHON_BUILD); \
	fi
	$(TARGET_MAKE_ENV) $(VUX9K_MICROPYTHON_ENV) $(MAKE) -C $(@D)/ports/unix \
		$(VUX9K_MICROPYTHON_MAKE_OPTS)
	touch $(VUX9K_MICROPYTHON_BUILD)/.vux9k-variant
	$(TARGET_CROSS)flthdr -p $(VUX9K_MICROPYTHON_BUILD)/micropython \
		> $(VUX9K_MICROPYTHON_BUILD)/flthdr.txt
	cat $(VUX9K_MICROPYTHON_BUILD)/flthdr.txt
	rm -rf $(VUX9K_MICROPYTHON_PYLIB)
	mkdir -p $(VUX9K_MICROPYTHON_PYLIB)
	for m in $(VUX9K_MICROPYTHON_BC_MODULES); do \
		(cd $(VUX9K_MICROPYTHON_BC_DIR) && \
			$(VUX9K_MICROPYTHON_MPYCROSS) -o $(VUX9K_MICROPYTHON_PYLIB)/$$m.mpy $$m.py) || exit 1; \
	done
endef

# MPY on the card only (VUX9K/LINUX/BIN/, on the PATH; an 8.3 name): the initramfs is RAM for
# good. The .mpy files into images/pylib/ (an earlier build's go first: a module no longer
# built would go to the card too), which make linux-dist makes into VUX9K/LINUX/PYLIB.TAR
define VUX9K_MICROPYTHON_INSTALL_IMAGES_CMDS
	$(INSTALL) -D -m 0644 $(VUX9K_MICROPYTHON_BUILD)/flthdr.txt $(BINARIES_DIR)/micropython-flthdr.txt
	$(INSTALL) -D -m 0755 $(VUX9K_MICROPYTHON_BUILD)/micropython \
		$(BINARIES_DIR)/sd/VUX9K/LINUX/BIN/MPY
	rm -rf $(BINARIES_DIR)/pylib
	$(foreach m,$(VUX9K_MICROPYTHON_BC_MODULES),\
		$(INSTALL) -D -m 0644 $(VUX9K_MICROPYTHON_PYLIB)/$(m).mpy \
			$(BINARIES_DIR)/pylib/$(m).mpy$(sep))
endef

$(eval $(generic-package))
