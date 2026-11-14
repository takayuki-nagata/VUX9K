################################################################################
#
# vux9k-sysmon
#
################################################################################

# The system monitor on the LEDs (docs/agents/linux.md, "LEDs and the system monitor"), in the
# initramfs (/usr/sbin/sysmon), which init starts at boot: Rust (cargo, the host's rustup
# toolchain from rust-toolchain.toml, no crates to fetch) and a C main from buildroot's
# toolchain (the userland's Makefile)
VUX9K_SYSMON_VERSION = 1.0
VUX9K_SYSMON_SITE = $(BR2_EXTERNAL_VUX9K_PATH)/../userland/sysmon
VUX9K_SYSMON_SITE_METHOD = local
VUX9K_SYSMON_LICENSE = MIT
# Not the host's cargo output (make test-sysmon-host, the Stop hook's cargo check)
VUX9K_SYSMON_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = --exclude target
# FLAT's stack is fixed at link time; the program's deepest frame holds a 1.5 KB file buffer
VUX9K_SYSMON_FLAT_STACKSIZE = 0x2000

define VUX9K_SYSMON_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(TARGET_CONFIGURE_OPTS) -C $(@D) sysmon
endef

define VUX9K_SYSMON_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/sysmon $(TARGET_DIR)/usr/sbin/sysmon
endef

$(eval $(generic-package))
