/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 specific neighbour information
 *
 * This is not to be included by the application.
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 */

#ifndef __IEEE802154_NBR_H__
#define __IEEE802154_NBR_H__

#include "nbr.h"

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
#include "ieee802154_tsch_nbr.h"
#endif

/**
 * @brief IEEE 802.15.4 neighbour table entry
 */
struct ieee802154_nbr_data {
	/* TODO: Add generic neighbour info, e.g. secDeviceDescriptor, see
	 * section 9.5, table 9-14
	 */

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
	/* TODO: Evolve this into a union of orthogonal protocol specific
	 * extensions if further protocols require L2-specific attributes.
	 */
	struct ieee802154_tsch_nbr_data tsch;
#endif
};

/**
 * @brief Retrieve the IEEE 802.15.4 neighbor table.
 *
 * @return The IEEE 802.15.4 neighbour table.
 */
struct net_nbr_table *ieee802154_nbr_table_get(void);

/**
 * @brief Get the IEEE 802.15.4 specific neighbor data from a neighbor entry.
 *
 * @param[in] nbr pointer to the neighbor entry
 *
 * @return The neighbor data from the given neighbor or NULL if the given
 * neighbor is invalid.
 */
static inline struct ieee802154_nbr_data *ieee802154_nbr_data(struct net_nbr *nbr)
{
	return nbr ? (struct ieee802154_nbr_data *)nbr->data : NULL;
}

/**
 * @brief Look up an IEEE 802.15.4 neighbor entry.
 *
 * @param[in] iface pointer to the interface on which to search for the link
 * @param[in] lladdr pointer to the link layer address to search for
 *
 * @return The neighbor data corresponding to the given link layer address (or
 * NULL if not found).
 */
struct ieee802154_nbr_data *ieee802154_nbr_data_lookup(struct net_if *iface,
						       struct net_linkaddr *lladdr);

#endif /* __IEEE802154_NBR_H__ */
