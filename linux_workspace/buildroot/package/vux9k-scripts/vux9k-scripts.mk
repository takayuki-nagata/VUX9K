################################################################################
#
# vux9k-scripts
#
################################################################################

# Sample #!/bin/sh scripts for the card (linux_workspace/userland/scripts/, outside this external
# tree: CI's cache key), in VUX9K/LINUX/BIN/ with the card's programs, on the login shell's PATH
# (docs/agents/linux.md, "Text tools and vi"). Nothing to build. vfat gives every file the
# mode 0777 & ~fmask, and init mounts the card without options, so fmask is init's umask (0022):
# executable. A name must be 8.3 without an extension (the card's), so the install stops at a
# longer one or one with a dot
VUX9K_SCRIPTS_VERSION = 1.0
VUX9K_SCRIPTS_SITE = $(BR2_EXTERNAL_VUX9K_PATH)/../userland/scripts
VUX9K_SCRIPTS_SITE_METHOD = local
VUX9K_SCRIPTS_LICENSE = MIT
VUX9K_SCRIPTS_NAMES = ledctl meminfo pylib

# On the card only: the initramfs is RAM for good
VUX9K_SCRIPTS_INSTALL_TARGET = NO
VUX9K_SCRIPTS_INSTALL_IMAGES = YES

define VUX9K_SCRIPTS_INSTALL_IMAGES_CMDS
	for s in $(VUX9K_SCRIPTS_NAMES); do \
		case $$s in ?????????* | *.*) echo "vux9k-scripts: $$s is not an 8.3 name"; exit 1 ;; esac; \
		$(INSTALL) -D -m 0755 $(@D)/$$s $(BINARIES_DIR)/sd/VUX9K/LINUX/BIN/$$s || exit 1; \
	done
endef

$(eval $(generic-package))
