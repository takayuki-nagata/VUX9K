################################################################################
#
# vux9k-bbtools
#
################################################################################

# The text tools: the card's busyboxes, one for each use (linux_workspace/userland/bbtools/: a
# fragment each over allnoconfig), so that each exec copies a small one into contiguous RAM
# (docs/agents/linux.md, "Text tools and vi"). The source is buildroot's busybox tarball and
# patches; the fragments and their Makefile are read where they are, outside this external tree
# (CI's cache key), and BUILD_CMDS writes each .config again, so make linux-rootfs's
# vux9k-bbtools-rebuild (VUX9K_BR_PACKAGES in the Makefile) builds what a changed fragment
# touches.
VUX9K_BBTOOLS_VERSION = $(BUSYBOX_VERSION)
VUX9K_BBTOOLS_SOURCE = busybox-$(BUSYBOX_VERSION).tar.bz2
VUX9K_BBTOOLS_SITE = $(BUSYBOX_SITE)
VUX9K_BBTOOLS_DL_SUBDIR = busybox
VUX9K_BBTOOLS_LICENSE = GPL-2.0
VUX9K_BBTOOLS_LICENSE_FILES = LICENSE
# The initramfs' busybox first: an applet it has stops the install (INSTALL_TARGET_CMDS)
VUX9K_BBTOOLS_DEPENDENCIES = busybox
VUX9K_BBTOOLS_INSTALL_IMAGES = YES
VUX9K_BBTOOLS_TOOLS_DIR = $(BR2_EXTERNAL_VUX9K_PATH)/../userland/bbtools
# Each busybox on the card (its name there, 8.3) with the highest order its exec's block may
# have (flthdr.sh stops the build above it): order 5, 128 KB, so that a pipe's processes find
# their blocks in free memory the card's page cache has left in pieces; awk alone needs
# ~155 KB and gets order 6, as the initramfs' busybox has
VUX9K_BBTOOLS_ORDERS = bbedit:5 bbgrep:5 bbsed:5 bbsort:5 bbawk:6 bbcalc:5 bbfiles:5 bbtar:5
VUX9K_BBTOOLS_TOOLS = $(foreach t,$(VUX9K_BBTOOLS_ORDERS),$(firstword $(subst :, ,$(t))))
VUX9K_BBTOOLS_OUT = $(@D)/vux9k
VUX9K_BBTOOLS_BIN = /mnt/sd/VUX9K/LINUX/BIN
# No VUX9K_BBTOOLS_FLAT_STACKSIZE: as buildroot's busybox, elf2flt -r only, and the stack is
# busybox's own (Makefile.flags' FLTFLAGS, -s 16000)

define VUX9K_BBTOOLS_BUSYBOX_PATCHES
	$(APPLY_PATCHES) $(@D) $(BUSYBOX_PKGDIR) \*.patch
endef
VUX9K_BBTOOLS_POST_PATCH_HOOKS += VUX9K_BBTOOLS_BUSYBOX_PATCHES

# package/busybox/busybox.mk's BUSYBOX_MAKE_ENV and BUSYBOX_MAKE_OPTS, without the install's;
# then flthdr.sh's check of each one's exec against its order (VUX9K_BBTOOLS_ORDERS), which
# stops the build above it, and its table (each one's size, `flthdr -p` and block)
define VUX9K_BBTOOLS_BUILD_CMDS
	$(TARGET_MAKE_ENV) CFLAGS="$(TARGET_CFLAGS)" CFLAGS_busybox="" \
		$(MAKE) -C $(VUX9K_BBTOOLS_TOOLS_DIR) \
		BBTOOLS_SRC=$(@D) BBTOOLS_OUT=$(VUX9K_BBTOOLS_OUT) \
		AR="$(TARGET_AR)" NM="$(TARGET_NM)" RANLIB="$(TARGET_RANLIB)" CC="$(TARGET_CC)" \
		ARCH=$(NORMALIZED_ARCH) EXTRA_LDFLAGS="$(TARGET_LDFLAGS)" \
		CROSS_COMPILE="$(TARGET_CROSS)" SKIP_STRIP=y \
		BBTOOLS_NAMES="$(VUX9K_BBTOOLS_TOOLS)" all
	sh $(VUX9K_BBTOOLS_TOOLS_DIR)/flthdr.sh $(TARGET_CROSS)flthdr $(VUX9K_BBTOOLS_OUT) \
		$(VUX9K_BBTOOLS_ORDERS) > $(VUX9K_BBTOOLS_OUT)/flthdr.txt || \
		{ cat $(VUX9K_BBTOOLS_OUT)/flthdr.txt; exit 1; }
endef

# The applets' links in the initramfs (/usr/bin/<applet> -> the busybox on the card, on the
# PATH once init has mounted it; vfat has no links), from each build's busybox.links. The links
# an earlier build made go first. An applet the initramfs has already (busybox's ls, head, ...)
# or another of the card's busyboxes has stops the install: a fragment that names it is wrong.
define VUX9K_BBTOOLS_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/bin
	find $(TARGET_DIR)/usr/bin -type l -lname '$(VUX9K_BBTOOLS_BIN)/bb*' -delete
	for t in $(VUX9K_BBTOOLS_TOOLS); do \
		for a in `sed 's|.*/||' $(VUX9K_BBTOOLS_OUT)/$$t/busybox.links`; do \
			for d in bin sbin usr/bin usr/sbin; do \
				if [ -e $(TARGET_DIR)/$$d/$$a ] || [ -L $(TARGET_DIR)/$$d/$$a ]; then \
					echo "vux9k-bbtools: $$t's $$a is in the initramfs already ($$d/$$a)"; \
					exit 1; \
				fi; \
			done; \
			ln -s $(VUX9K_BBTOOLS_BIN)/$$t $(TARGET_DIR)/usr/bin/$$a || exit 1; \
		done; \
	done
endef

# Each on the card (VUX9K/LINUX/BIN/, which make linux-dist copies; an earlier build's bb* go
# first, as a busybox no longer built would go to the card too), and flthdr.sh's table as
# images/bbtools-flthdr.txt
define VUX9K_BBTOOLS_INSTALL_IMAGES_CMDS
	$(INSTALL) -D -m 0644 $(VUX9K_BBTOOLS_OUT)/flthdr.txt $(BINARIES_DIR)/bbtools-flthdr.txt
	rm -f $(BINARIES_DIR)/sd/VUX9K/LINUX/BIN/bb*
	$(foreach t,$(VUX9K_BBTOOLS_TOOLS),\
		$(INSTALL) -D -m 0755 $(VUX9K_BBTOOLS_OUT)/$(t)/busybox \
			$(BINARIES_DIR)/sd/VUX9K/LINUX/BIN/$(t)$(sep))
endef

$(eval $(generic-package))
