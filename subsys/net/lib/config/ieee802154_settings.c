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

#ifdef CONFIG_NET_L2_IEEE802154_TSCH

#include "ieee802154_utils.h"

#if defined(CONFIG_NET_CONFIG_IEEE802154_DEVICE_ROLE_PAN_COORDINATOR)
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_PAN_COORDINATOR
#elif defined(CONFIG_NET_CONFIG_IEEE802154_DEVICE_ROLE_COORDINATOR)
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_COORDINATOR
#else
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_ENDDEVICE
#endif

#else

#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_ENDDEVICE

#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

int z_net_config_ieee802154_setup(struct net_if *iface)
{
	const struct device *const dev = iface == NULL ? DEVICE_DT_GET(DT_CHOSEN(zephyr_ieee802154))
						       : net_if_get_device(iface);
	int16_t tx_power = CONFIG_NET_CONFIG_IEEE802154_RADIO_TX_POWER;
	uint16_t channel = CONFIG_NET_CONFIG_IEEE802154_CHANNEL;
	uint16_t device_role = NET_CONFIG_IEEE802154_DEVICE_ROLE;
	uint16_t pan_id, short_addr;
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

	if (!IS_ENABLED(CONFIG_NET_L2_IEEE802154_TSCH) ||
	    device_role == IEEE802154_DEVICE_ROLE_PAN_COORDINATOR) {
		pan_id = CONFIG_NET_CONFIG_IEEE802154_PAN_ID;
	} else {
		pan_id = IEEE802154_PAN_ID_NOT_ASSOCIATED;
	}

	if (!IS_ENABLED(CONFIG_NET_L2_IEEE802154_TSCH) ||
	    device_role != IEEE802154_DEVICE_ROLE_PAN_COORDINATOR) {
		short_addr = IEEE802154_SHORT_ADDRESS_NOT_ASSOCIATED;
	} else {
		short_addr = 0x0000;
	}

	if (net_mgmt(NET_REQUEST_IEEE802154_SET_CHANNEL, iface, &channel, sizeof(uint16_t))) {
		return -EINVAL;
	}

	if (net_mgmt(NET_REQUEST_IEEE802154_SET_DEVICE_ROLE, iface, &device_role,
		     sizeof(int16_t)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_TX_POWER, iface, &tx_power, sizeof(int16_t)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_PAN_ID, iface, &pan_id, sizeof(uint16_t)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_SHORT_ADDR, iface, &short_addr, sizeof(uint16_t))) {
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
