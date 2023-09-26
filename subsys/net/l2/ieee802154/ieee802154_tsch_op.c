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

#include <zephyr/kernel.h>
#include <zephyr/net/ieee802154_radio.h>
#include <zephyr/net/ieee802154_tracing.h>
#include <zephyr/net/net_core.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_linkaddr.h>
#include <zephyr/net/net_time.h>
#include <zephyr/random/rand32.h>
#include <zephyr/sys/util.h>
#include <zephyr/toolchain.h>

#include "ieee802154_frame.h"
#include "ieee802154_nbr.h"
#include "ieee802154_priv.h"
#include "ieee802154_tsch_nbr.h"
#include "ieee802154_tsch_op.h"
#include "ieee802154_tsch_schedule.h"
#include "ieee802154_utils.h"

BUILD_ASSERT(IS_ENABLED(CONFIG_NET_PKT_TXTIME),
	     "TSCH requires TX timestamps, please enable CONFIG_NET_PKT_TXTIME.");

/* We only define a single thread and slot timing context for now as we assume
 * that even if multiple L2 interfaces are configured they will participate in a
 * single schedule to avoid collisions and timing delays.
 */

static K_KERNEL_STACK_DEFINE(tsch_thread_stack, CONFIG_NET_L2_IEEE802154_TSCH_STACK_SIZE);
static struct k_thread tsch_thread;

static struct tsch_slot_context {
	/* TODO: Support multiple interfaces. */
	struct net_if *iface;

	/* The currently active link, NULL as soon as TX/RX succeeded or failed. */
	struct ieee802154_tsch_link *current_link;

	/* The nanosecond precision syntonized absolute network uptime at which
	 * the last scheduled slot started. As slot timings are measured in
	 * microseconds, these values will never have to be rounded.
	 */
	net_time_t current_slot_start;

	/* In case of an RX slot, the expected RX time will be stored here to
	 * determine the time correction.
	 */
	net_time_t programmed_tx_rx_time;

	/* The actual network uptime counter tick's time to which the slot start
	 * was programmed after syntonization and tick conversion.
	 */
	net_time_t next_active_slot_programmed_expiry;

	/* The nanosecond precision offset to the next active timeslot measured
	 * from current_slot_start, must not be rounded.
	 */
	net_time_t next_active_slot_offset;
} tsch_slot_context;

static inline uint16_t tsch_calculate_channel(struct ieee802154_context *ctx,
					      uint16_t channel_offset)
{
	struct ieee802154_hopping_sequence *hs;
	uint16_t index_of_offset, channel;

	if (k_sem_take(&ctx->ctx_lock, K_NO_WAIT)) {
		LOG_ERR("Could not lock context in TSCH operation callback.");
		return IEEE802154_NO_CHANNEL;
	}

	/* see section 6.2.6.3 */
	hs = ctx->hopping_sequence;
	index_of_offset = (ctx->tsch_asn + channel_offset) % hs->length;
	channel = hs->list[index_of_offset];

	k_sem_give(&ctx->ctx_lock);

	return channel;
}

/* Executes the current link in a timeslot. This function blocks until the end
 * of the timeslot.
 *
 * Note: This is called from ISR context so locking must be immediate. All data
 * accessed in this function must be immutable while in this function.
 */
static void tsch_operate_link(struct net_if *iface, struct ieee802154_tsch_link *active_link,
			      struct ieee802154_tsch_link *backup_link)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	tsch_slot_context.current_link = active_link;
	uint16_t previous_channel, channel;

	ieee802154_trace_enter(NULL, IEEE802154_TRACING_TSCH_SLOT_START);
	LOG_DBG("timeslot started");

	/* Channel Hopping */
	channel = tsch_calculate_channel(ctx, tsch_slot_context.current_link->channel_offset);
	if (channel == IEEE802154_NO_CHANNEL) {
		goto out;
	}

	k_sem_take(&ctx->ctx_lock, K_USEC(1));
	previous_channel = ctx->channel;
	ctx->channel = channel;
	k_sem_give(&ctx->ctx_lock);

	/* Operate timeslot. */
	while (tsch_slot_context.current_link) {
		struct net_pkt *tx_pkt = NULL;

		/* TODO: support coordinator role in addition to PAN coordinator,
		 * i.e. as soon as a device is being associated that has
		 * coordinator role, start sending beacons for that device as
		 * well.
		 */
		if (IS_ENABLED(CONFIG_NET_CONFIG_IEEE802154_DEVICE_ROLE_PAN_COORDINATOR) &&
		    tsch_slot_context.current_link->advertising) {
			tx_pkt = ieee802154_create_enh_beacon(iface, true);
		} else if (tsch_slot_context.current_link->tx) {
			int ret;

			ret = ieee802154_tsch_unqueue_packet(
				iface, &tsch_slot_context.current_link->node_addr, &tx_pkt);
			if (ret == -ENODATA) {
				if (tsch_slot_context.current_link == active_link) {
					tsch_slot_context.current_link = backup_link;
					continue;
				}

				break;
			}
		}

		if (tx_pkt) {
			/** For TX we need to set the channel explicitly. */
			if (previous_channel != channel &&
			    ieee802154_radio_set_channel(iface, channel)) {
				LOG_ERR("Could not hop to channel %u.", channel);
			}

			/* No need for locking as the timeslot template is
			 * immutable while TSCH is on.
			 */
			tsch_slot_context.programmed_tx_rx_time =
				tsch_slot_context.current_slot_start +
				ctx->tsch_timeslot_template.tx_offset * NSEC_PER_USEC;
			net_pkt_set_timestamp_ns(tx_pkt, tsch_slot_context.programmed_tx_rx_time);

			(void)ieee802154_radio_send(iface, tx_pkt, tx_pkt->buffer);

			/* TODO: Re-schedule (prepend) TSCH transmission if
			 * ret != 0 (e.g. -EBUSY), see TSCH CCA & TSCH CSMA/CA.
			 */
			net_pkt_unref(tx_pkt);
			break;
		} else {
			net_time_t rx_start, rx_duration;
			struct ieee802154_config config;

			__ASSERT_NO_MSG(tsch_slot_context.current_link->rx);

			/* The expected RX time is macTsRxOffset + macTsRxWait/2,
			 * see section 6.5.4.3. No need for locking as the
			 * timeslot template is immutable while TSCH is on.
			 */
			rx_start = tsch_slot_context.current_slot_start +
				   ctx->tsch_timeslot_template.rx_offset * NSEC_PER_USEC;
			rx_duration = ctx->tsch_timeslot_template.rx_wait * NSEC_PER_USEC;
			tsch_slot_context.programmed_tx_rx_time = rx_start + rx_duration / 2;

			if (ieee802154_radio_get_hw_capabilities(iface) & IEEE802154_HW_RX_TX_ACK) {
				/* Synchronization needs to be implemented by the
				 * driver in case it implements auto-ACK, see
				 * section 6.5.4.3.
				 */
				config = (struct ieee802154_config){
					.expected_rx_time =
						tsch_slot_context.programmed_tx_rx_time};
				(void)ieee802154_radio_configure(
					iface, IEEE802154_CONFIG_EXPECTED_RX_TIME, &config);
			}

			config = (struct ieee802154_config){
				.rx_slot = {
					.start = rx_start,
					.duration = rx_duration,
					.channel = channel,
				}};
			(void)ieee802154_radio_configure(iface, IEEE802154_CONFIG_RX_SLOT, &config);

			break;
		}
	}

out:
	ieee802154_trace_exit(IEEE802154_TRACING_TSCH_SLOT_END);
	LOG_DBG("timeslot ended");
}

void ieee802154_tsch_handle_time_correction(struct net_if *iface, int16_t time_correction_us)
{
	const struct net_time_reference_api *time_api;
	k_timepoint_t programmed_tx_rx_time;
	net_time_t actual_tx_rx_time;

	if (!tsch_slot_context.current_link || !tsch_slot_context.current_link->timekeeping ||
	    !tsch_slot_context.programmed_tx_rx_time) {
		return;
	}

	time_api = ieee802154_radio_get_time_reference(iface);

	if (time_api->get_timepoint_from_time(time_api, tsch_slot_context.programmed_tx_rx_time,
					      NET_TIME_ROUNDING_NEAREST_TIMEPOINT,
					      &programmed_tx_rx_time)) {
		return;
	}

	actual_tx_rx_time =
		tsch_slot_context.programmed_tx_rx_time + time_correction_us * NSEC_PER_USEC;

	time_api->syntonize(time_api, actual_tx_rx_time, programmed_tx_rx_time);
}

enum net_verdict ieee802154_tsch_handle_rx(struct net_if *iface, struct net_linkaddr *rx_ll_addr,
					   net_time_t pkt_timestamp_ns, int16_t *time_correction_us)
{
	struct net_linkaddr *link_ll_addr;

	if (!tsch_slot_context.current_link || !tsch_slot_context.current_link->rx ||
	    !tsch_slot_context.programmed_tx_rx_time) {
		*time_correction_us = 0;
		return NET_DROP;
	}

	link_ll_addr = &tsch_slot_context.current_link->node_addr;
	if (!net_linkaddr_cmp(link_ll_addr, rx_ll_addr)) {
		*time_correction_us = 0;
		return NET_DROP;
	}

	*time_correction_us = DIV_ROUND_CLOSEST(
		tsch_slot_context.programmed_tx_rx_time - pkt_timestamp_ns, NSEC_PER_USEC);

	ieee802154_tsch_handle_time_correction(iface, *time_correction_us);

	return NET_CONTINUE;
}

#if defined(CONFIG_ASSERT) && CONFIG_ASSERT_LEVEL > 0
/* Must be called from ISR context for precise timing. */
static void tsch_assert_active_link(struct net_time_timer *net_time_timer)
{
	net_time_t now;

	ARG_UNUSED(net_time_timer);

	__ASSERT_NO_MSG(
		net_time_reference_get_time(
			ieee802154_radio_get_time_reference(tsch_slot_context.iface), &now) == 0);
	__ASSERT_NO_MSG(tsch_slot_context.next_active_slot_programmed_expiry == now);
}
#else
#define tsch_assert_active_link(timer) NULL
#endif

static K_NET_TIME_TIMER_DEFINE(tsch_timer, tsch_assert_active_link, NULL);

/* Blocks until the start of the next active link. */
static inline void tsch_sleep_until_next_active_link(struct net_time_timer *net_time_timer)
{
	net_time_reference_timer_start(
		ieee802154_radio_get_time_reference(tsch_slot_context.iface), net_time_timer,
		tsch_slot_context.current_slot_start + tsch_slot_context.next_active_slot_offset, 0,
		NET_TIME_ROUNDING_NEAREST_TIMEPOINT,
		&tsch_slot_context.next_active_slot_programmed_expiry);

	/* TODO: Measure overhead of this vs. other solutions (semaphore, mutex). */
	k_timer_status_sync(&net_time_timer->timer);

	tsch_slot_context.current_slot_start += tsch_slot_context.next_active_slot_offset;
}

static void tsch_state_machine(void *p1, void *p2, void *p3)
{
	struct net_if *iface = (struct net_if *)p1;
	struct ieee802154_context *ctx;

	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	LOG_DBG("TSCH mode on");

	ctx = net_if_l2_data(iface);
	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	if (ieee802154_is_associated(ctx)) {
		k_sem_give(&ctx->ctx_lock);

		tsch_slot_context.iface = iface;
		(void)net_time_counter_wake_up(ieee802154_radio_get_time_reference(iface));
		if (net_time_reference_get_time(ieee802154_radio_get_time_reference(iface),
						&tsch_slot_context.current_slot_start)) {
			LOG_ERR("Cannot retrieve high resolution start time.");
			return;
		}

		k_sem_take(&ctx->ctx_lock, K_FOREVER);
	}

	while (ctx->tsch_mode) {
		if (ieee802154_is_associated(ctx)) {
			struct ieee802154_tsch_link *next_active_link, *backup_link;

			k_sem_give(&ctx->ctx_lock);

			next_active_link = ieee802154_tsch_schedule_get_next_active_link(
				iface, &tsch_slot_context.next_active_slot_offset, &backup_link);

			tsch_sleep_until_next_active_link(&tsch_timer);

			if (likely(next_active_link)) {
				tsch_operate_link(iface, next_active_link, backup_link);
			} else {
				LOG_ERR("No link scheduled.");
			}

			k_sem_take(&ctx->ctx_lock, K_FOREVER);

		} else {
			k_sem_give(&ctx->ctx_lock);

			LOG_DBG("waiting for association");
			k_sleep(K_SECONDS(1U));

			k_sem_take(&ctx->ctx_lock, K_FOREVER);
		}
	}

	LOG_DBG("TSCH mode off");
}

#define TSCH_REQUIRED_HW_CAPS (IEEE802154_HW_RXTIME | IEEE802154_HW_TXTIME)

int ieee802154_tsch_mode_on(struct net_if *iface)
{
	enum ieee802154_hw_caps hw_caps = ieee802154_radio_get_hw_capabilities(iface);
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	int ret = 0;

	if ((hw_caps & TSCH_REQUIRED_HW_CAPS) != TSCH_REQUIRED_HW_CAPS) {
		LOG_ERR("TSCH protocol requires a driver that supports "
			"timed RX and TX capabilities.");
		return -ENOTSUP;
	}

	k_sem_take(&ctx->scan_ctx_lock, K_FOREVER);

	if (ctx->tsch_mode) {
		ret = EALREADY;
		goto out;
	}

	ctx->tsch_mode = true;

	if (hw_caps & IEEE802154_HW_RX_TX_ACK) {
		/* Inject Time Correction IE to be used in enhanced ACK packets
		 * for offloading of synchronization if the driver implements
		 * auto-ACK, see section 6.5.4.3.
		 */
		struct ieee802154_header_ie header_ie =
			IEEE802154_DEFINE_HEADER_IE_TIME_CORRECTION(true, 0);

		/* The time correction IE is to be injected in all enhanced ACK
		 * frames.
		 */
		struct ieee802154_config config = {
			.ack_ie = {.header_ie = &header_ie,
				   .short_addr = IEEE802154_BROADCAST_ADDRESS},
		};

		(void)ieee802154_radio_configure(iface, IEEE802154_CONFIG_ENH_ACK_HEADER_IE,
						 &config);
	}

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

	if ((ieee802154_radio_get_hw_capabilities(iface) & TSCH_REQUIRED_HW_CAPS) !=
	    TSCH_REQUIRED_HW_CAPS) {
		LOG_ERR("TSCH protocol requires a driver that supports "
			"timed RX and TX capabilities.");
		return;
	}

	if (IS_ENABLED(CONFIG_SEGGER_SYSTEMVIEW)) {
		ieee802154_tracing_init();
	}

	k_thread_create(&tsch_thread, tsch_thread_stack, K_KERNEL_STACK_SIZEOF(tsch_thread_stack),
			tsch_state_machine, iface, NULL, NULL, TSCH_METAIRQ_PRIO, 0, K_FOREVER);

	k_thread_name_set(&tsch_thread, "ieee802154_tsch");

	/* TODO: Distinguish between TSCH data shared by all interfaces and
	 *       interface-specific data.
	 */

	/* see section 8.4.3.3.1, table 8-96 */
	ctx->tsch_join_metric = 1U;
	ctx->tsch_disconnect_time = 0xff;

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
