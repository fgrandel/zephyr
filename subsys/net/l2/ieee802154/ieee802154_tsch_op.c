/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 TSCH operation implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_ieee802154_tsch, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include <zephyr/net/ieee802154_radio.h>

#include "ieee802154_tsch_op.h"
#include "ieee802154_utils.h"

BUILD_ASSERT(IS_ENABLED(CONFIG_NET_PKT_TXTIME),
	     "TSCH requires TX timestamps, please enable CONFIG_NET_PKT_TXTIME.");

/* We only define a single thread and slot timing context for now as we assume
 * that even if multiple L2 interfaces are configured they will participate in a
 * single schedule to avoid collisions and timing delays.
 */
static K_KERNEL_STACK_DEFINE(tsch_thread_stack, CONFIG_NET_L2_IEEE802154_TSCH_STACK_SIZE);
static struct k_thread tsch_thread;

static void tsch_state_machine(void *p1, void *p2, void *p3)
{
	struct net_if *iface = (struct net_if *)p1;
	struct ieee802154_context *ctx;

	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	LOG_DBG("TSCH mode on");

	ctx = net_if_l2_data(iface);
	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	while (ctx->tsch_mode) {
		if (ieee802154_is_associated(ctx)) {
			LOG_INF("TSCH slot operation");
		} else {
			LOG_INF("TSCH waiting for association");
		}

		k_sem_give(&ctx->ctx_lock);
		k_sleep(K_SECONDS(1U));
		k_sem_take(&ctx->ctx_lock, K_FOREVER);
	}

	k_sem_give(&ctx->ctx_lock);

	LOG_DBG("TSCH mode off");
}

int ieee802154_tsch_mode_on(struct net_if *iface)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	int ret = 0;

	k_sem_take(&ctx->scan_ctx_lock, K_FOREVER);

	if (ctx->tsch_mode) {
		ret = EALREADY;
		goto out;
	}

	ctx->tsch_mode = true;

	/* TODO: Implement NO_SYNC (ENETDOWN), see 8.2.19.6, table 8-50: The MAC
	 * layer was not synchronized to a network.
	 */

	k_thread_start(&tsch_thread);

out:
	k_sem_give(&ctx->scan_ctx_lock);
	return ret;
}

int ieee802154_tsch_mode_off(struct net_if *iface)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	int ret = 0;

	k_sem_take(&ctx->scan_ctx_lock, K_FOREVER);

	if (!ctx->tsch_mode) {
		ret = EALREADY;
		goto out;
	}

	ctx->tsch_mode = false;

out:
	k_sem_give(&ctx->scan_ctx_lock);
	return ret;
}

BUILD_ASSERT(
	CONFIG_NUM_METAIRQ_PRIORITIES > 0,
	"TSCH expects a Meta IRQ, please set CONFIG_NUM_METAIRQ_PRIORITIES to a non-zero value.");
#define TSCH_METAIRQ_PRIO K_PRIO_COOP(0)

/* It is assumed that this function is called while the context is not yet published. */
void ieee802154_tsch_op_init(struct net_if *iface)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	enum ieee802154_phy_channel_page channel_page;
	bool is_subghz;

	k_thread_create(&tsch_thread, tsch_thread_stack, K_KERNEL_STACK_SIZEOF(tsch_thread_stack),
			tsch_state_machine, iface, NULL, NULL, TSCH_METAIRQ_PRIO, 0, K_FOREVER);

	k_thread_name_set(&tsch_thread, "ieee802154_tsch");

	/* see section 8.4.3.3.1, table 8-96 */
	ctx->tsch_join_metric = 1U;

	channel_page = ieee802154_radio_current_channel_page(iface);
	switch (channel_page) {
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_ZERO_OQPSK_2450_BPSK_868_915:
		/* Check whether the 686 or 915 bands are supported on this page. */
		is_subghz = ieee802154_radio_verify_channel(iface, 0) ||
			    ieee802154_radio_verify_channel(iface, 1);
		break;

	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_TWO_OQPSK_868_915:
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_FIVE_OQPSK_780:
	/* Currently only SubG FSK channels are supported by existing drivers -
	 * needs determine the actual band once drivers support more than one
	 * band
	 */
	case IEEE802154_ATTR_PHY_CHANNEL_PAGE_NINE_SUN_PREDEFINED:
		is_subghz = true;

	default:
		is_subghz = false;
	}
	/* see section 8.4.3.3.4, table 8-99 */
	ctx->tsch_timeslot_template = (struct ieee802154_tsch_timeslot_template){
		.cca_offset = 1800U,
		.cca = 128U,
		.tx_offset = is_subghz ? 2800U : 2120U,
		.rx_offset = is_subghz ? 1800U : 1020U,
		.rx_ack_delay = 800U,
		.tx_ack_delay = 1000U,
		.rx_wait = is_subghz ? 6000U : 2200U,
		.rx_tx = is_subghz ? 1000U : 192U,
		.max_ack = is_subghz ? 6000U : 2400U,
		.max_tx = is_subghz ? 103040U : 4256U,
		.length = is_subghz ? 120000U : 10000U,
		.ack_wait = 400U,
	};

	/* This is just a default, can be changed via NET_REQUEST_IEEE802154_SET_TSCH_MODE. */
	ctx->tsch_cca = IS_ENABLED(CONFIG_NET_L2_IEEE802154_RADIO_TSCH_CCA);
}
