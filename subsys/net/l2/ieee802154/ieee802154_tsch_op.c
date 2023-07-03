/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH operation implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_ieee802154_tsch, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include <zephyr/net/ieee802154_radio.h>

#include "ieee802154_tsch_op.h"
#include "ieee802154_utils.h"

/* It is assumed that this function is called while the context is not yet published. */
void ieee802154_tsch_op_init(struct net_if *iface)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	enum ieee802154_phy_channel_page channel_page;
	bool is_subghz;

	channel_page = ieee802154_radio_current_channel_page(iface);
	switch (channel_page) {
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_ZERO_OQPSK_2450_BPSK_868_915:
		/* Check whether the 686 or 915 bands are supported on this page. */
		is_subghz = ieee802154_radio_verify_channel(iface, 0) ||
			    ieee802154_radio_verify_channel(iface, 1);
		break;

	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_TWO_OQPSK_868_915:
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_FIVE_OQPSK_780:
	/* Currently only SubG FSK channels are supported by existing drivers -
	 * needs determine the actual band once drivers support more than one
	 * band
	 */
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_NINE_SUN_PREDEFINED:
		is_subghz = true;

	default:
		is_subghz = false;
	}
	/* see section 8.4.3.3.4, table 8-99 */
	ctx->tsch_timeslot_template = (struct ieee802154_tsch_timeslot_template){
		.cca_offset = 1800U,
		.cca = 128U,
		.tx_offset = is_subghz ? 2800U : 2120U,
		.rx_offset = is_subghz ? 1800U : 1020U,
		.rx_ack_delay = 800U,
		.tx_ack_delay = 1000U,
		.rx_wait = is_subghz ? 6000U : 2200U,
		.rx_tx = is_subghz ? 1000U : 192U,
		.max_ack = is_subghz ? 6000U : 2400U,
		.max_tx = is_subghz ? 103040U : 4256U,
		.length = is_subghz ? 120000U : 10000U,
		.ack_wait = 400U,
	};
}
