/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_ieee802154_tsch, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include "ieee802154_priv.h"
#include "ieee802154_utils.h"

#include <zephyr/net/net_if.h>

/* See section 6.2.5.2 - TSCH CCA algorithm. */
static inline int tsch_channel_access(struct net_if *iface)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);

	/* No need to lock the context as tsch_cca is immutable while TSCH mode is on. */
	if (ctx->tsch_cca) {
		int ret = ieee802154_radio_cca(iface);

		if (ret == 0) {
			/* Channel is idle -> CCA Success */
			return 0;
		} else if (ret != -EBUSY) {
			/* CCA exited with failure code -> CSMA Abort */
			return -EIO;
		}

		/* TODO: re-schedule the packet once TSCH operation
		 *       is actually implemented.
		 */

		/* TODO: implement TSCH CSMA/CA procedure for shared
		 *       links, see section 6.2.5.3.
		 */
		return -EBUSY;
	}

	return 0;
}

/* Declare the public channel access algorithm function used by L2. */
FUNC_ALIAS(tsch_channel_access, ieee802154_wait_for_clear_channel, int);
