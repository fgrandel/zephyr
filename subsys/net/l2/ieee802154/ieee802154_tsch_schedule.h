/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH slotframes and links
 *
 * This is not to be included by the application.
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 */

#ifndef __IEEE802154_TSCH_SCHEDULE_H__
#define __IEEE802154_TSCH_SCHEDULE_H__

#include <zephyr/net/ieee802154.h>
#include <zephyr/net/ieee802154_tsch.h>
#include <zephyr/net/net_time.h>

/**
 * @brief Identify next active link.
 *
 * @param[in] iface pointer to the IEEE 802.15.4 interface
 * @param[out] next_active_slot_offset time from the start of the current
 * timeslot (as represented by the current ASN) to the next active link
 * @param[out] backup_link pointer to a secondary link if the returned (i.e.
 * primary) link cannot be used, i.e. for a Tx-only link, if there is no
 * outgoing packet in the queue. In that case, run the backup link instead. The
 * backup link must have the Rx flag set.
 *
 * @return pointer to the primary link
 */
struct ieee802154_tsch_link *
ieee802154_tsch_schedule_get_next_active_link(struct net_if *iface,
					      net_time_t *next_active_slot_offset,
					      struct ieee802154_tsch_link **backup_link);

#endif /* __IEEE802154_TSCH_SCHEDULE_H__ */
