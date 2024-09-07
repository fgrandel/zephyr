/* IEEE 802.15.4 settings code */

/*
 * Copyright (c) 2017 Intel Corporation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_config, CONFIG_NET_CONFIG_LOG_LEVEL);

#include <zephyr/kernel.h>
#include <errno.h>

#include <zephyr/net/net_if.h>
#include <zephyr/net/net_core.h>
#include <zephyr/net/net_mgmt.h>
#include <zephyr/net/ieee802154_mgmt.h>

#include "init_common.h"
#include "init_ieee802154.h"

/* By simple encapsulation/information hiding principles, the radio driver should
 * not know about L3+ (nor the other way round). We therefore do not want to mix
 * up radio driver and interface settings in the same file! We can do that
 * because no generator forces a structure on us.
 *
 * Note: Due to MAC offloading, the driver *could* know about offloaded parts of
 * L2.
 */
struct net_config_ieee802154_radio_driver {
	const struct device *dev;
	const uint8_t ext_addr[IEEE802154_EXT_ADDR_LENGTH];
	uint16_t pan_id;
	uint16_t default_channel;
#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	const char sec_key[16];
	uint8_t sec_key_mode;
	uint8_t sec_level;
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */
	int16_t default_tx_pwr;
	bool ack_required;
};

/* Normalization (and pragmatic driver pointer resolution) require us to attach
 * driver hard and soft settings to the same abstract configuration entity. This
 * does not violate encapsulation because hard and soft settings will be defined
 * in different files (and even formats). Also soft settings will be determined
 * relative to an interface configuration node while hard settings are relative
 * to driver nodes with a given hardware compat.
 *
 * This is the reason why we cannot use inheritance to model a combination of
 * hard and soft settings in a single binding hierarchy. We want them to be
 * separate "mixins" contributing to the same driver node. That's why we compose
 * two separate compats (each with its own binding) on one node:
 * - one compat to determine the hardware-specific programming model (hard
 *   settings) and
 * - another compat to determine the hardware-independent driver programming
 *   model (soft settings).
 *
 * NOTE: Both, hard and soft settings, are still radio *driver* settings in the
 * most literal sense (see ieee802154_radio.h) and not interface settings. This
 * is a good example why `struct driver` is not the correct way to distinguish
 * between hard and soft settings.
 */
#define NET_CONFIG_IEEE802154_RADIO_DRIVER_STATE(_radio_driver_node_id, _ieee802154_state_name)    \
	({                                                                                         \
		 .dev = DEVICE_DT_GET(_radio_driver_node_id),                                      \
		 .pan_id = DT_PROP(_radio_driver_node_id, pan_id),                                 \
		 .ext_addr = DT_PROP(_radio_driver_node_id, ext_addr),                             \
		 .default_channel = DT_PROP(_radio_driver_node_id, default_channel),               \
		 .default_tx_pwr = DT_PROP(_radio_driver_node_id, default_tx_pwr),                 \
		 .sec_key = DT_PROP(_radio_driver_node_id, sec_key),                               \
		 .sec_key_mode = DT_PROP(_radio_driver_node_id, sec_key_mode),                     \
		 .sec_level = DT_PROP(_radio_driver_node_id, sec_level),                           \
		 .ack_required = !DT_PROP(_radio_driver_node_id, no_ack),                          \
	},)

#define NET_CONFIG_IEEE802154_RADIO_DRIVER(_iface_node_id)                                         \
	IF_ENABLED(DT_NODE_HAS_COMPAT_STATUS(DT_PARENT(_iface_node_id),                            \
					     zephyr_net_l2_ieee802154_native, okay),               \
		   NET_CONFIG_IEEE802154_RADIO_DRIVER_STATE(                                       \
			   DT_PARENT(_iface_node_id),                                              \
			   NET_CONFIG_SUBSTATE_NAME(_iface_node_id, ieee802154)))

/* TODO: instantiate config */

int z_net_config_ieee802154_setup(struct net_if *iface)
{
	uint16_t channel = CONFIG_NET_CONFIG_IEEE802154_CHANNEL;
	uint16_t pan_id = CONFIG_NET_CONFIG_IEEE802154_PAN_ID;
	const struct device *const dev = iface == NULL ? DEVICE_DT_GET(DT_CHOSEN(zephyr_ieee802154))
						       : net_if_get_device(iface);
	int16_t tx_power = CONFIG_NET_CONFIG_IEEE802154_RADIO_TX_POWER;

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	struct ieee802154_security_params sec_params = {
		.key = CONFIG_NET_CONFIG_IEEE802154_SECURITY_KEY,
		.key_len = sizeof(CONFIG_NET_CONFIG_IEEE802154_SECURITY_KEY),
		.key_mode = CONFIG_NET_CONFIG_IEEE802154_SECURITY_KEY_MODE,
		.level = CONFIG_NET_CONFIG_IEEE802154_SECURITY_LEVEL,
	};
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	if (!device_is_ready(dev)) {
		return -ENODEV;
	}

	if (!iface) {
		iface = net_if_lookup_by_dev(dev);
		if (!iface) {
			return -ENOENT;
		}
	}

	if (IS_ENABLED(CONFIG_NET_CONFIG_IEEE802154_ACK_REQUIRED)) {
		if (net_mgmt(NET_REQUEST_IEEE802154_SET_ACK, iface, NULL, 0)) {
			return -EIO;
		}
	}

	if (net_mgmt(NET_REQUEST_IEEE802154_SET_PAN_ID,
		     iface, &pan_id, sizeof(uint16_t)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_CHANNEL,
		     iface, &channel, sizeof(uint16_t)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_TX_POWER,
		     iface, &tx_power, sizeof(int16_t))) {
		return -EINVAL;
	}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	if (net_mgmt(NET_REQUEST_IEEE802154_SET_SECURITY_SETTINGS, iface,
		     &sec_params, sizeof(struct ieee802154_security_params))) {
		return -EINVAL;
	}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	if (!IS_ENABLED(CONFIG_IEEE802154_NET_IF_NO_AUTO_START)) {
		/* The NET_IF_NO_AUTO_START flag was set by the driver, see
		 * ieee802154_init() to allow for configuration before starting
		 * up the interface.
		 */
		net_if_flag_clear(iface, NET_IF_NO_AUTO_START);
		net_if_up(iface);
	}

	return 0;
}
