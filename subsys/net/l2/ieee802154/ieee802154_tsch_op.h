/*
 * Copyright (c) 2022 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH operation
 *
 * This is not to be included by the application.
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 */

#ifndef __IEEE802154_TSCH_OP_H__
#define __IEEE802154_TSCH_OP_H__

#ifdef CONFIG_NET_L2_IEEE802154_TSCH

#include <zephyr/net/net_core.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_time.h>

#include "ieee802154_frame.h"

/**
 * Lets TSCH handle all valid incoming frames.
 *
 * @details While TSCH mode is on and if acknowledgment was requrested by the
 * sender (AR bit equals one), the caller of this function is expected to
 * acknowledge the frame independently of the return value of this function with
 * an Enh-Ack frame including a Time Correction IE, see section 6.5.4.2.
 *
 * The time correction value and acknowledgment status to be included in the
 * Time Correction IE will be determined by this function.
 *
 * @param[in] iface The interface on which the frame was received.
 * @param[in] ll_addr The LL address of the received frame.
 * @param[in] pkt_timestamp_ns Precise timepoint at which the frame's end of SFD
 * was present at the local antenna.
 * @param[out] time_correction_us The positive or negative difference from the
 * expected reception time in microseconds.
 *
 * @retval NET_CONTINUE if successful. The caller is expected to positively
 * acknowlege the frame and then pass it on to the next higher layer.
 *
 * @retval NET_DROP The received frame does not match the active link or cannot
 * be accepted for other reasons at this time. The caller is expected to
 * negatively acknowlege the frame and then drop it.
 */
enum net_verdict ieee802154_tsch_handle_rx(struct net_if *iface, struct net_linkaddr *ll_addr,
					   net_time_t pkt_timestamp_ns,
					   int16_t *time_correction_us);

/**
 * Lets TSCH handle all time incoming correction.
 *
 * @details This method checks whether the current link belongs to a timekeeping
 * neighbor. If this is the case, then the given time correction value will be
 * used to discipline the local network clock. See section 6.5.4.3.
 *
 * @param[in] iface The interface on which the frame was received.
 * @param[out] time_correction_us The positive or negative difference from the
 * expected RX time as determined by the neighbor (acknowledge-based
 * synchronization) or this device (frame-based synchronization) in
 * microseconds.
 */
void ieee802154_tsch_handle_time_correction(struct net_if *iface, int16_t time_correction_us);

/**
 * @brief Enter TSCH mode and start the TSCH state machine.
 *
 * @details If the device is configured as a PAN co-ordinator it will start
 * advertising enhanced beacons in advertising links. This requires that the
 * initial slotframe and link tables have been populated by the upper layer so
 * that joining devices can communicate with the PAN co-ordinator.
 *
 * Any other device will have to be synchronized to an existing TSCH network
 * already, i.e. it must have received an enhanced beacon advertising the
 * network, synchronized to its ASN and timeslot configuration and storing the
 * advertised slotframes and links into its MAC PIB database.
 *
 * @param iface A pointer to the network interface.
 *
 * @return 0 if the network was successfully started, EALREADY if the network
 * was already started, ENETDOWN if the device was not synchronized to a network
 * before calling this function.
 */
int ieee802154_tsch_mode_on(struct net_if *iface);

/**
 * @brief Stop the TSCH state machine and leave TSCH mode.
 *
 * @details If the device is configured as a PAN co-ordinator it will stop
 * advertising enhanced beacons, any device will stop using slotframes and links
 * when calling this function.
 *
 * @param iface A pointer to the network interface.
 *
 * @return 0 if the network was successfully started, EALREADY if the network
 * was already started, ENETDOWN if the device was not synchronized to a network
 * before calling this function.
 */
int ieee802154_tsch_mode_off(struct net_if *iface);

/**
 * @brief Initialize the TSCH specific parts of the IEEE 802.15.4 L2 driver's
 * context.
 *
 * @details It is assumed that this function is called while the context is not
 * yet published, so no locking is needed.
 *
 * @param iface A pointer to the network interface.
 */
void ieee802154_tsch_op_init(struct net_if *iface);

#else /* CONFIG_NET_L2_IEEE802154_TSCH */

#define ieee802154_tsch_handle_rx(...) NET_CONTINUE
#define ieee802154_tsch_op_init(...)
#define ieee802154_tsch_handle_time_correction(...)

#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

#endif /* __IEEE802154_TSCH_OP_H__ */
