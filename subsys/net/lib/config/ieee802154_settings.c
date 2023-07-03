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

#include <zephyr/net/ieee802154_tsch.h>

#include "ieee802154_utils.h"

#if defined(CONFIG_NET_CONFIG_IEEE802154_DEVICE_ROLE_PAN_COORDINATOR)
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_PAN_COORDINATOR
#elif defined(CONFIG_NET_CONFIG_IEEE802154_DEVICE_ROLE_COORDINATOR)
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_COORDINATOR
#else
#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_ENDDEVICE
#endif

#define SLOTFRAME_HANDLE 0

static struct ieee802154_tsch_slotframe slotframe = {
	.handle = SLOTFRAME_HANDLE,
	.size = 13, /* Prime so that overlapping links will not be shadowed. */
};

#define LINK_HANDLE 0

static uint8_t broadcast_address_be[] = {0xff, 0xff};
static struct ieee802154_tsch_link link = {
	.handle = LINK_HANDLE,
	.slotframe_handle = SLOTFRAME_HANDLE,
	.timeslot = 0,
	.node_addr = {.addr = broadcast_address_be,
		      .len = IEEE802154_SHORT_ADDR_LENGTH,
		      .type = NET_LINK_IEEE802154},
	.tx = 1,
	.advertising = 1, /* used to advertise the enhanced beacon */
	.timekeeping = 1,
};

#else

#define NET_CONFIG_IEEE802154_DEVICE_ROLE IEEE802154_DEVICE_ROLE_ENDDEVICE

#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

#ifdef CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT

#define NUM_HOPPING_CHANNELS                                                                       \
	(CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_TO -                                           \
	 CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_FROM + 1)

#if CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_FROM == 0 &&                                        \
	CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_TO == 0
#define HOPPING_CHANNEL_LIST IEEE802154_CHANNEL_HOPPING_SEQUENCE_SUB_GHZ_1_1
#elif CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_FROM == 1 &&                                      \
	CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_TO == 10
#define HOPPING_CHANNEL_LIST IEEE802154_CHANNEL_HOPPING_SEQUENCE_SUB_GHZ_10_10
#elif CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_FROM == 11 &&                                     \
	CONFIG_NET_CONFIG_IEEE802154_CHANNEL_RANGE_TO == 26
#define HOPPING_CHANNEL_LIST IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_16_16
#else
#error "Unsupported hopping sequence."
#endif

static uint16_t hopping_sequence_list[NUM_HOPPING_CHANNELS] = HOPPING_CHANNEL_LIST;
static struct ieee802154_hopping_sequence hopping_sequence = {
	.list = hopping_sequence_list,
	.length = NUM_HOPPING_CHANNELS,
};

#endif /* CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT */

int z_net_config_ieee802154_setup(struct net_if *iface)
{
	const struct device *const dev = iface == NULL ? DEVICE_DT_GET(DT_CHOSEN(zephyr_ieee802154))
						       : net_if_get_device(iface);
	int16_t tx_power = CONFIG_NET_CONFIG_IEEE802154_RADIO_TX_POWER;
	uint16_t device_role = NET_CONFIG_IEEE802154_DEVICE_ROLE;
	uint16_t pan_id, short_addr;
#ifdef CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT
	enum ieee802154_phy_channel_page channel_page;
#else
	uint16_t channel = CONFIG_NET_CONFIG_IEEE802154_CHANNEL;
#endif /* CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT */
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

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
	if (net_mgmt(NET_REQUEST_IEEE802154_SET_TSCH_SLOTFRAME, iface, &slotframe,
		     sizeof(void *)) ||
	    net_mgmt(NET_REQUEST_IEEE802154_SET_TSCH_LINK, iface, &link, sizeof(void *))) {
		return -EINVAL;
	}
#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

#ifdef CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT
	channel_page = ieee802154_radio_current_channel_page(iface);
	if (channel_page != IEEE802154_ATTR_PHY_CHANNEL_PAGE_ZERO_OQPSK_2450_BPSK_868_915 &&
	    channel_page != IEEE802154_ATTR_PHY_CHANNEL_PAGE_TWO_OQPSK_868_915) {
		return -ENOTSUP;
	}

	if (net_mgmt(NET_REQUEST_IEEE802154_SET_HOPPING_SEQUENCE, iface, &hopping_sequence,
		     sizeof(void *))) {
		return -EINVAL;
	}
#else
	if (net_mgmt(NET_REQUEST_IEEE802154_SET_CHANNEL, iface, &channel, sizeof(uint16_t))) {
		return -EINVAL;
	}
#endif /* CONFIG_NET_L2_IEEE802154_CHANNEL_HOPPING_SUPPORT */

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
