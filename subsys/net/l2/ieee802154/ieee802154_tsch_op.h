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

#include <zephyr/net/net_if.h>

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
#endif /* __IEEE802154_TSCH_OP_H__ */
