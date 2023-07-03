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
