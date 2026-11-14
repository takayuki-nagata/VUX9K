################################################################################
#
# vux9k-init
#
################################################################################

# This tree's init (docs/agents/linux.md, "Init"), as /init: buildroot's cpio installs its own
# /init script only where there is none, and the kernel runs /init of an initramfs
VUX9K_INIT_VERSION = 1.0
VUX9K_INIT_SITE = $(BR2_EXTERNAL_VUX9K_PATH)/../userland/init
VUX9K_INIT_SITE_METHOD = local
VUX9K_INIT_LICENSE = MIT
VUX9K_INIT_FLAT_STACKSIZE = 0x2000

define VUX9K_INIT_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(TARGET_CONFIGURE_OPTS) -C $(@D) init
endef

define VUX9K_INIT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/init $(TARGET_DIR)/init
endef

$(eval $(generic-package))
