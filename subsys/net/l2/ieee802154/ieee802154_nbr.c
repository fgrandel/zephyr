/*
 * Copyright (c) 2022 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 specific neighbour information implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include "nbr.h"

#include "ieee802154_nbr.h"

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_ieee802154, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

static void ieee802154_nbr_remove(struct net_nbr *nbr)
{
	NET_DBG("IEEE 802.15.4 neighbour %p removed", nbr);
}

NET_NBR_POOL_INIT(ieee802154_nbr_pool, CONFIG_NET_L2_IEEE802154_MAX_NEIGHBORS,
		  sizeof(struct ieee802154_nbr_data), ieee802154_nbr_remove, 0);

static void ieee802154_neighbor_table_clear(struct net_nbr_table *table)
{
	NET_DBG("IEEE 802.15.4 neighbor table %p cleared", table);
}

NET_NBR_TABLE_INIT(NET_NBR_LOCAL, nbr_table_ieee802154, ieee802154_nbr_pool,
		   ieee802154_neighbor_table_clear);

inline struct net_nbr_table *ieee802154_nbr_table_get(void)
{
	return &net_nbr_table_ieee802154.table;
}

inline struct ieee802154_nbr_data *ieee802154_nbr_data_lookup(struct net_if *iface,
							      struct net_linkaddr *lladdr)
{
	struct net_nbr *nbr = net_nbr_lookup(&net_nbr_table_ieee802154.table, iface, lladdr);

	return ieee802154_nbr_data(nbr);
}
