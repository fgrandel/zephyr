/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Hybrid network uptime counter and reference for TI CC13/26xx SoC
 *
 * This is not to be included by the application.
 */

#ifndef ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_NET_TIME_H_
#define ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_NET_TIME_H_

/**
 * @brief Retrieves a singleton instance of the CC13xx/CC26xx network reference
 * time API for driver-internal usage.
 */
const struct net_time_reference_api *ieee802154_cc13xx_cc26xx_net_time_reference_api_get(void);

#endif /* ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_NET_TIME_H_ */
