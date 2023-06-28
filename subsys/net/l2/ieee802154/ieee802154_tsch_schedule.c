/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH slotframes and links implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_ieee802154_tsch, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include <zephyr/net/ieee802154.h>
#include <zephyr/net/net_if.h>
#include <zephyr/sys/atomic.h>

#include "nbr.h"

#include "ieee802154_nbr.h"
#include "ieee802154_tsch_schedule.h"

struct ieee802154_tsch_link *default_tsch_link_comparator(struct net_if *iface,
							  struct ieee802154_tsch_link *a,
							  struct ieee802154_tsch_link *b)
{
	/* Prioritize Tx links, then prioritize by handle. */
	if (a->tx == b->tx) {
		/* Both or neither are Tx links: select the one with lowest
		 * slotframe handle (see section 6.2.6.4).
		 */
		if (a->slotframe_handle != b->slotframe_handle) {
			return a->slotframe_handle < b->slotframe_handle ? a : b;
		}

		/* Both links are Rx links or belong to the same neighbor: select
		 * the one with the lowest link handle.
		 */
		if (!a->tx || net_linkaddr_cmp(&a->node_addr, &b->node_addr)) {
			return a->handle < b->handle ? a : b;
		}

		/* Both are Tx links and belong to different neighbors: select
		 * the one with most packets to send
		 */
		struct ieee802154_tsch_nbr_data *an =
			&ieee802154_nbr_data_lookup(iface, &a->node_addr)->tsch;
		struct ieee802154_tsch_nbr_data *bn =
			&ieee802154_nbr_data_lookup(iface, &b->node_addr)->tsch;
		int a_packet_count = an ? an->tx_queue_size : 0;
		int b_packet_count = bn ? bn->tx_queue_size : 0;

		if (a_packet_count == b_packet_count) {
			return a->handle < b->handle ? a : b;
		}

		/* Compare the number of packets in the queue */
		return a_packet_count > b_packet_count ? a : b;
	}

	/* Select the link that has the Tx option. */
	return a->tx ? a : b;
}

struct ieee802154_tsch_link *
ieee802154_tsch_schedule_get_next_active_link(struct net_if *iface,
					      net_time_t *next_active_slot_offset,
					      struct ieee802154_tsch_link **backup_link)
{
	struct ieee802154_tsch_link *curr_best = NULL, *curr_backup = NULL;
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	struct ieee802154_tsch_slotframe *slotframe;
	uint16_t time_to_curr_best = 1;

	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	/* For each slotframe, look for the earliest occurring link. */
	SYS_SFLIST_FOR_EACH_CONTAINER(&ctx->tsch_slotframe_table, slotframe, sfnode) {
		/* Get the timeslot from the ASN, given the slotframe length. */
		uint16_t timeslot = ctx->tsch_asn % slotframe->size;
		struct ieee802154_tsch_link *link;

		SYS_SFLIST_FOR_EACH_CONTAINER(&slotframe->link_table, link, sfnode) {
			uint16_t time_to_timeslot;

			time_to_timeslot = link->timeslot > timeslot
						   ? link->timeslot - timeslot
						   : slotframe->size + link->timeslot - timeslot;

			if (curr_best == NULL || time_to_timeslot < time_to_curr_best) {
				time_to_curr_best = time_to_timeslot;
				curr_best = link;
				curr_backup = NULL;
			} else if (time_to_timeslot == time_to_curr_best) {
				struct ieee802154_tsch_link *new_best =
					default_tsch_link_comparator(iface, curr_best, link);

				if (link == new_best) {

					/* If the current link replaced the previous best
					 * link and the previous best link is an Rx link,
					 * it might still be useful as a backup link.
					 */
					if (curr_best->rx &&
					    (curr_backup == NULL ||
					     curr_best->slotframe_handle <
						     curr_backup->slotframe_handle)) {
						curr_backup = curr_best;
					}

				} else {

					/* If the current link is not the new best link
					 * but is an Rx link, it might still be useful as
					 * a backup link.
					 */
					if (link->rx &&
					    (curr_backup == NULL ||
					     slotframe->handle < curr_backup->slotframe_handle)) {
						curr_backup = link;
					}
				}

				curr_best = new_best;
			}
		}
	}

	if (next_active_slot_offset != NULL) {
		ctx->tsch_asn += time_to_curr_best;
		__ASSERT_NO_MSG(ctx->tsch_asn % IEEE802154_TSCH_MAX_ASN == ctx->tsch_asn);
		*next_active_slot_offset =
			time_to_curr_best * ctx->tsch_timeslot_template.length * NSEC_PER_USEC;
	}

	k_sem_give(&ctx->ctx_lock);

	if (backup_link != NULL) {
		*backup_link = curr_backup;
	}

	return curr_best;
}
