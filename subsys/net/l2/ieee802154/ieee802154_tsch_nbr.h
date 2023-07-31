/*
3* Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH neighbor information
 *
 * This is not to be included by the application.
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 */

#ifndef __IEEE802154_TSCH_NBR_H__
#define __IEEE802154_TSCH_NBR_H__

#include <zephyr/kernel.h>
#include <zephyr/net/net_pkt.h>
#include <zephyr/sys/util.h>

/**
 * @brief TSCH neighbor information
 */
struct ieee802154_tsch_nbr_data {
	/* Tx packet queue of this neighbour. The @ref net_pkt struct already
	 * contains the necessary queue item header. We may only queue packets
	 * that we own.
	 */
	struct k_fifo tx_queue;
	/* The approximate number of queued packets - used to prioritize Tx
	 * slots. We use k_fifo + atomic_val_t as it has less space overhead
	 * than k_msgq (which also keeps track of the number of used items) and
	 * we only need approximate values anyway.
	 */
	atomic_val_t tx_queue_size;

	/* CSMA backoff window (number of slots to skip) */
	uint16_t backoff_window;
	/* CSMA backoff exponent */
	uint8_t backoff_exponent;
	/* is this neighbor a virtual neighbor used for broadcast (of data packets or EBs) */
	uint8_t is_broadcast: 1;
	/* is this neighbor a time source? */
	uint8_t is_time_source: 1;
	/* How many links do we have to this neighbor? */
	uint8_t tx_links_count;
	/* How many dedicated links do we have to this neighbor? */
	uint8_t dedicated_tx_links_count;
};

/**
 * @brief Add a packet to a neighbor's TX queue.
 *
 * @details Adds a packet to the neighbor's TX queue if the same packet had not
 * been added to the queue before.
 *
 * @param[in] pkt pointer to the packet to be added
 *
 * @retval 0 if the packet was added to the neighbor's TX queue.
 * @retval -ENOENT if the neighbor table does not have a free slot for the
 * packet's destination address.
 */
int ieee802154_tsch_queue_packet(struct net_pkt *pkt);

/**
 * @brief Remove a packet from a neighbor's TX queue.
 *
 * @details Removes the next packet from a neighbor's TX queue on a given
 * interface if at least one packet is currently waiting in that queue.
 *
 * @param[in] iface pointer to the interface to which the packet has been queued
 * @param[in] addr pointer to the neighbor's link layer address to which the
 * packet has been queued
 * @param[out] pkt pointer to a packet pointer which will be assigned the
 * unqueued packet
 *
 * @retval 0 if the packet was added to the neighbor's TX queue.
 * @retval -ENODATA if no packet is waiting in the queue.
 */
int ieee802154_tsch_unqueue_packet(struct net_if *iface, struct net_linkaddr *addr,
				   struct net_pkt **pkt);
#endif /* __IEEE802154_TSCH_NBR_H__ */