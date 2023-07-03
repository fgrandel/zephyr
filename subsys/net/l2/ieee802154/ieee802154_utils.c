/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 internal MAC and PHY Utils Implementation
 *
 * All references to the standard in this file cite IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(net_ieee802154, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include "ieee802154_utils.h"
#ifdef CONFIG_NET_L2_IEEE802154_TSCH
#include <zephyr/net/ieee802154_tsch.h>
#endif

/**
 * PHY utilities
 */

bool ieee802154_radio_verify_channel(struct net_if *iface, uint16_t channel)
{
	struct ieee802154_attr_value value;

	if (channel == IEEE802154_NO_CHANNEL) {
		return false;
	}

	if (ieee802154_radio_attr_get(iface, IEEE802154_ATTR_PHY_SUPPORTED_CHANNEL_RANGES,
				      &value)) {
		return false;
	}

	for (int channel_range_index = 0;
	     channel_range_index < value.phy_supported_channels->num_ranges;
	     channel_range_index++) {
		const struct ieee802154_phy_channel_range *const channel_range =
			&value.phy_supported_channels->ranges[channel_range_index];

		if (channel >= channel_range->from_channel &&
		    channel <= channel_range->to_channel) {
			return true;
		}
	}

	return false;
}

uint16_t ieee802154_radio_number_of_channels(struct net_if *iface)
{
	struct ieee802154_attr_value value;
	uint16_t num_channels = 0;

	if (ieee802154_radio_attr_get(iface, IEEE802154_ATTR_PHY_SUPPORTED_CHANNEL_RANGES,
				      &value)) {
		return 0;
	}

	for (int channel_range_index = 0;
	     channel_range_index < value.phy_supported_channels->num_ranges;
	     channel_range_index++) {
		const struct ieee802154_phy_channel_range *const channel_range =
			&value.phy_supported_channels->ranges[channel_range_index];

		__ASSERT_NO_MSG(channel_range->to_channel >= channel_range->from_channel);
		num_channels += channel_range->to_channel - channel_range->from_channel + 1U;
	}

	return num_channels;
}

/**
 * MAC utilities
 */

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
static inline struct ieee802154_tsch_slotframe *
get_slotframe_with_predecessor(struct ieee802154_context *ctx, uint8_t handle,
			       struct ieee802154_tsch_slotframe **predecessor)
{
	struct ieee802154_tsch_slotframe *current;

	*predecessor = NULL;

	SYS_SFLIST_FOR_EACH_CONTAINER(&ctx->tsch_slotframe_table, current, sfnode) {
		if (current->handle == handle) {
			return current;
		} else if (current->handle > handle) {
			break;
		}

		*predecessor = current;
	}

	return NULL;
}

static inline struct ieee802154_tsch_slotframe *
delete_slotframe_and_get_predecessor(struct ieee802154_context *ctx, uint8_t handle,
				     struct ieee802154_tsch_slotframe **predecessor)
{
	struct ieee802154_tsch_slotframe *found =
		get_slotframe_with_predecessor(ctx, handle, predecessor);

	if (found) {
		sys_sflist_remove(&ctx->tsch_slotframe_table, &(*predecessor)->sfnode,
				  &found->sfnode);
	}

	return found;
}

static inline struct ieee802154_tsch_slotframe *get_slotframe(struct ieee802154_context *ctx,
							      uint8_t handle)
{
	struct ieee802154_tsch_slotframe *predecessor;

	return get_slotframe_with_predecessor(ctx, handle, &predecessor);
}

struct ieee802154_tsch_slotframe *
ieee802154_ctx_tsch_delete_slotframe(struct ieee802154_context *ctx, uint8_t handle)
{
	struct ieee802154_tsch_slotframe *predecessor;

	return delete_slotframe_and_get_predecessor(ctx, handle, &predecessor);
}

struct ieee802154_tsch_slotframe *
ieee802154_ctx_tsch_set_slotframe(struct ieee802154_context *ctx,
				  struct ieee802154_tsch_slotframe *slotframe)
{
	struct ieee802154_tsch_slotframe *replaced, *predecessor;

	replaced = delete_slotframe_and_get_predecessor(ctx, slotframe->handle, &predecessor);
	sys_sflist_insert(&ctx->tsch_slotframe_table, &predecessor->sfnode, &slotframe->sfnode);

	return replaced;
}

struct ieee802154_tsch_link *ieee802154_ctx_tsch_delete_link(struct ieee802154_context *ctx,
							     uint16_t handle)
{
	struct ieee802154_tsch_link *predecessor, *current;
	struct ieee802154_tsch_slotframe *slotframe;

	SYS_SFLIST_FOR_EACH_CONTAINER(&ctx->tsch_slotframe_table, slotframe, sfnode) {
		predecessor = NULL;

		SYS_SFLIST_FOR_EACH_CONTAINER(&slotframe->link_table, current, sfnode) {
			if (current->handle == handle) {
				sys_sflist_remove(&slotframe->link_table, &predecessor->sfnode,
						  &current->sfnode);

				/* Safe loop not needed as we leave the loop
				 * after removing the node.
				 */
				return current;
			}

			predecessor = current;
		}
	}

	return NULL;
}

struct ieee802154_tsch_link *ieee802154_ctx_tsch_set_link(struct ieee802154_context *ctx,
							  struct ieee802154_tsch_link *link)
{
	struct ieee802154_tsch_link *replaced, *predecessor = NULL, *current;
	struct ieee802154_tsch_slotframe *slotframe;

	replaced = ieee802154_ctx_tsch_delete_link(ctx, link->handle);

	slotframe = get_slotframe(ctx, link->slotframe_handle);
	__ASSERT_NO_MSG(slotframe);

	if (!slotframe) {
		return NULL;
	}

	SYS_SFLIST_FOR_EACH_CONTAINER(&slotframe->link_table, current, sfnode) {
		/* The list is sorted by timeslot and handle. */
		if (current->timeslot > link->timeslot ||
		    (current->timeslot == link->timeslot && current->handle > link->handle)) {
			break;
		}

		predecessor = current;
	}

	/* Keep outside loop to support empty list case. */
	sys_sflist_insert(&slotframe->link_table, &predecessor->sfnode, &link->sfnode);

	return replaced;
}
#endif
