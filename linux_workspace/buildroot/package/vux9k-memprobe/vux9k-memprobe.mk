################################################################################
#
# vux9k-memprobe
#
################################################################################

# The pattern for this tree's own programs (docs/agents/linux.md, "Userland packages"): the
# source in linux_workspace/userland/<name>/, outside this external tree (CI's cache key), rsynced
# by buildroot (local site) and rebuilt on every make linux-rootfs (VUX9K_BR_PACKAGES in the
# Makefile); installed into the initramfs and, for the card, into images/sd/, which make
# linux-dist copies as it is (VUX9K/LINUX/BIN/)
VUX9K_MEMPROBE_VERSION = 1.0
VUX9K_MEMPROBE_SITE = $(BR2_EXTERNAL_VUX9K_PATH)/../userland/memprobe
VUX9K_MEMPROBE_SITE_METHOD = local
VUX9K_MEMPROBE_LICENSE = MIT
VUX9K_MEMPROBE_INSTALL_IMAGES = YES
# FLAT's stack is fixed at link time (elf2flt -s); the program's buffers are static
VUX9K_MEMPROBE_FLAT_STACKSIZE = 0x2000

define VUX9K_MEMPROBE_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(TARGET_CONFIGURE_OPTS) -C $(@D) memprobe
endef

define VUX9K_MEMPROBE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/memprobe $(TARGET_DIR)/usr/bin/memprobe
endef

define VUX9K_MEMPROBE_INSTALL_IMAGES_CMDS
	$(INSTALL) -D -m 0755 $(@D)/memprobe $(BINARIES_DIR)/sd/VUX9K/LINUX/BIN/memprobe
endef

$(eval $(generic-package))
