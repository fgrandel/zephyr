/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH neighbor information implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */
#include <zephyr/kernel.h>
#include <zephyr/net/ieee802154_pkt.h>

#include "nbr.h"

#include "ieee802154_frame.h"
#include "ieee802154_nbr.h"
#include "ieee802154_tsch_nbr.h"

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_ieee802154_tsch, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

static int ieee802154_tsch_nbr_get(struct net_if *iface, struct net_linkaddr *addr,
				   struct ieee802154_tsch_nbr_data **nbr)
{
	struct ieee802154_nbr_data *nbr_data = ieee802154_nbr_data_lookup(iface, addr);

	if (nbr_data) {
		*nbr = &nbr_data->tsch;
		return 0;
	}

	*nbr = NULL;
	return -ENOENT;
}

int ieee802154_tsch_queue_packet(struct net_pkt *pkt)
{
	struct net_if *iface = net_pkt_iface(pkt);
	struct ieee802154_tsch_nbr_data *nbr;
	int ret;

	ret = ieee802154_tsch_nbr_get(iface, &pkt->lladdr_dst, &nbr);
	if (ret) {
		return ret;
	}

	k_fifo_put(&nbr->tx_queue, pkt);
	/* No need to lock the queue for counting as the queue size is just used
	 * as an approximate indicator for back pressure.
	 */
	if (IS_ENABLED(CONFIG_ASSERT) && CONFIG_ASSERT_LEVEL > 0 &&
	    !IS_ENABLED(CONFIG_NET_CONTEXT_NET_PKT_POOL)) {
		atomic_val_t previous_queue_size = atomic_inc(&nbr->tx_queue_size);

		__ASSERT_NO_MSG(previous_queue_size < CONFIG_NET_PKT_TX_COUNT);
	} else {
		(void)atomic_inc(&nbr->tx_queue_size);
	}

	return 0;
}

int ieee802154_tsch_unqueue_packet(struct net_if *iface, struct net_linkaddr *addr,
				   struct net_pkt **pkt)
{
	struct ieee802154_tsch_nbr_data *nbr;
	int ret;

	ret = ieee802154_tsch_nbr_get(iface, addr, &nbr);
	if (ret) {
		return ret;
	}

	*pkt = k_fifo_get(&nbr->tx_queue, K_NO_WAIT);
	if (*pkt) {
		/* No need to lock the queue for counting as the queue size is
		 * just used as an approximate indicator for back pressure.
		 */
		if (IS_ENABLED(CONFIG_ASSERT) && CONFIG_ASSERT_LEVEL > 0) {
			atomic_val_t previous_queue_size = atomic_dec(&nbr->tx_queue_size);

			__ASSERT_NO_MSG(previous_queue_size > 0);
		} else {
			(void)atomic_dec(&nbr->tx_queue_size);
		}

		return 0;
	}

	return -ENODATA;
}
