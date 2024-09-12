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
#include <zephyr/net/net_config.h>
#include <zephyr/net/net_core.h>
#include <zephyr/net/net_mgmt.h>
#include <zephyr/net/ieee802154_mgmt.h>

#include "macro_common.h"

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
#define NET_CONFIG_IEEE802154_RADIO_DRIVER_STATE(_drv_node_id)                                     \
	{.dev = DEVICE_DT_GET(_drv_node_id),                                                       \
	 .pan_id = DT_PROP(_drv_node_id, pan_id),                                                  \
	 .ext_addr = DT_PROP(_drv_node_id, ext_addr),                                              \
	 .default_channel = DT_PROP(_drv_node_id, default_channel),                                \
	 .default_tx_pwr = DT_PROP(_drv_node_id, default_tx_pwr),                                  \
	 .ack_required = !DT_PROP(_drv_node_id, no_ack),                                           \
	 IF_ENABLED(CONFIG_NET_L2_IEEE802154_SECURITY,                                             \
		    (.sec_key = DT_PROP_OR(_drv_node_id, sec_key, NULL),                           \
		     .sec_level = DT_PROP(_drv_node_id, sec_level),                                \
		     .sec_key_mode = DT_PROP(_drv_node_id, sec_key_mode),))},

#define NET_CONFIG_IEEE802154_RADIO_DRIVER_CONFIG_VERIFY(_drv_node_id)                             \
	IF_ENABLED(CONFIG_NET_L2_IEEE802154_SECURITY,                                              \
		   (BUILD_ASSERT(DT_NODE_HAS_PROP(_drv_node_id, sec_key) &&                        \
					 sizeof(DT_PROP_OR(_drv_node_id, sec_key, "")) == 16,      \
				 "A 16 byte security key is required when "                        \
				 "CONFIG_NET_L2_IEEE802154_SECURITY = y.");))

DT_FOREACH_STATUS_OKAY(zephyr_net_l2_ieee802154_native,
		       NET_CONFIG_IEEE802154_RADIO_DRIVER_CONFIG_VERIFY)

/* TODO: test with both compats, test w/ and w/o sec enabled. */
static struct net_config_ieee802154_radio_driver net_config_ieee802154_radio_drivers[] = {
	DT_FOREACH_STATUS_OKAY(zephyr_net_l2_ieee802154_native,
			       NET_CONFIG_IEEE802154_RADIO_DRIVER_STATE)
		DT_FOREACH_STATUS_OKAY(zephyr_net_l2_ieee802154,
				       NET_CONFIG_IEEE802154_RADIO_DRIVER_STATE)};

static struct net_config_ieee802154_radio_driver *get_cfg_by_dev(const struct device *dev)
{
	if (!dev) {
		return NULL;
	}

	ARRAY_FOR_EACH_PTR(net_config_ieee802154_radio_drivers, cfg) {
		if (cfg->dev == dev) {
			return cfg;
		}
	}

	return NULL;
}

int net_config_init_ieee802154(struct net_if *iface, bool start)
{
	struct net_config_ieee802154_radio_driver *cfg;
	const struct device *dev = NULL;
	uint16_t channel, pan_id;
	int16_t tx_power;
	int ifindex, ret;

	if (iface) {
		dev = net_if_get_device(iface);
	}

	if (dev == NULL) {
		dev = DEVICE_DT_GET_OR_NULL(DT_CHOSEN(zephyr_ieee802154));
		if (dev) {
			iface = net_if_lookup_by_dev(dev);
			if (!iface) {
				NET_WARN("DT chosen radio driver is not a network iface driver.");
				return -ENOENT;
			}
		}

		NET_DBG("No iface given: using %s", "DT chosen iface");
	}

	if (dev == NULL) {
		iface = net_if_get_default();
		if (iface) {
			dev = net_if_get_device(iface);
		} else {
			NET_WARN("No configurable iface found: ignoring config.");
			return -ENOENT;
		}

		NET_DBG("No iface given: using %s", "default iface");
	}

	NET_ASSERT(iface && dev);

	ifindex = net_if_get_by_iface(iface);

	if (!device_is_ready(dev)) {
		NET_WARN("Iface %d is not ready", ifindex);
		return -ENODEV;
	}

	if (net_if_is_up(iface) || !net_if_flag_is_set(iface, NET_IF_NO_AUTO_START)) {
		NET_WARN("Iface %d is already up", ifindex);
		return -EBUSY;
	}

	cfg = get_cfg_by_dev(dev);
	if (!cfg) {
		NET_WARN("No configuration found for iface %d", ifindex);
		return -ENOENT;
	}

	ret = cfg->ack_required ? net_mgmt(NET_REQUEST_IEEE802154_SET_ACK, iface, NULL, 0)
				: net_mgmt(NET_REQUEST_IEEE802154_UNSET_ACK, iface, NULL, 0);
	if (ret && ret != EALREADY) {
		NET_WARN("Could not configure %s of iface %d (%d)", "ACK", ifindex, ret);
		return -EIO;
	}

	ret = net_mgmt(NET_REQUEST_IEEE802154_SET_PAN_ID, iface, &pan_id, sizeof(uint16_t));
	if (ret && ret != EALREADY) {
		NET_WARN("Could not configure %s of iface %d (%d)", "PAN id", ifindex, ret);
		return -EINVAL;
	}

	ret = net_mgmt(NET_REQUEST_IEEE802154_SET_CHANNEL, iface, &channel, sizeof(uint16_t));
	if (ret && ret != EALREADY) {
		NET_WARN("Could not configure %s of iface %d (%d)", "channel", ifindex, ret);
		return -EINVAL;
	}

	ret = net_mgmt(NET_REQUEST_IEEE802154_SET_TX_POWER, iface, &tx_power, sizeof(int16_t));
	if (ret && ret != EALREADY) {
		NET_WARN("Could not configure %s of iface %d (%d)", "TX power", ifindex, ret);
		return -EINVAL;
	}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	struct ieee802154_security_params sec_params = {
		.key_len = sizeof(sec_params.key),
		.key_mode = cfg->sec_key_mode,
		.level = cfg->sec_level,
	};
	memcpy(sec_params.key, cfg->sec_key, sizeof(sec_params.key));

	ret = net_mgmt(NET_REQUEST_IEEE802154_SET_SECURITY_SETTINGS, iface, &sec_params,
		       sizeof(struct ieee802154_security_params));
	if (ret) {
		NET_WARN("Could not configure %s of iface %d (%d)", "sec params", ifindex, ret);
		return -EINVAL;
	}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	/* The NET_IF_NO_AUTO_START flag was set by the driver to allow for
	 * configuration before starting up the interface (see ieee802154_init()). So
	 * we need to start it up manually if requested.
	 */
	if (start) {
		net_if_flag_clear(iface, NET_IF_NO_AUTO_START);
		net_if_up(iface);
	}

	return 0;
}
