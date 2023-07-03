/*
 * Copyright (c) 2022 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 MAC frame information element (IE) related function implementations.
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_ieee802154_frame_ie, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include "ieee802154_frame.h"
#include "ieee802154_frame_ie.h"
#include "ieee802154_tsch_op.h"
#include "ieee802154_utils.h"

#include <zephyr/net/ieee802154.h>
#include <zephyr/net/ieee802154_radio.h>
#include <zephyr/net/net_if.h>
#include <zephyr/sys/sflist.h>

int ieee802154_parse_header_ies(uint8_t *start, uint8_t remaining_length,
				struct ieee802154_header_ies *header_ies)
{
	struct ieee802154_header_ie *header_ie;
	uint8_t *cursor = start;
	uint8_t element_id;
	int progress;

	if (remaining_length == 0) {
		/* invalid frame - at least one header IE was expected */
		return -EFAULT;
	}

	do {
		progress = IEEE802154_HEADER_IE_HEADER_LENGTH;

		if (remaining_length < progress) {
			return -EFAULT;
		}

		header_ie = (struct ieee802154_header_ie *)cursor;
		remaining_length -= progress;
		cursor += progress;

		if (header_ie->type != IEEE802154_IE_TYPE_HEADER) {
			return -EINVAL;
		}

		progress = header_ie->length;

		if (remaining_length < progress) {
			return -EFAULT;
		}

		element_id = ieee802154_header_ie_get_element_id(header_ie);

		switch (element_id) {
		case IEEE802154_HEADER_IE_ELEMENT_ID_CSL_IE:
			if (progress == sizeof(struct ieee802154_header_ie_csl_full)) {
				header_ies->csl_with_rendezvous_time = true;
			} else if (progress == sizeof(struct ieee802154_header_ie_csl_reduced)) {
				header_ies->csl_with_rendezvous_time = false;
			} else {
				return -EFAULT;
			}
			header_ies->csl = &header_ie->content.csl;
			break;

		case IEEE802154_HEADER_IE_ELEMENT_ID_RIT_IE:
			if (progress != sizeof(struct ieee802154_header_ie_rit)) {
				return -EFAULT;
			}

			header_ies->rit = &header_ie->content.rit;
			break;

		case IEEE802154_HEADER_IE_ELEMENT_ID_RENDEZVOUS_TIME_IE:
			if (progress != sizeof(struct ieee802154_header_ie_rendezvous_time)) {
				return -EFAULT;
			}

			header_ies->rendezvous_time = &header_ie->content.rendezvous_time;
			break;

		case IEEE802154_HEADER_IE_ELEMENT_ID_TIME_CORRECTION_IE:
			if (progress != sizeof(struct ieee802154_header_ie_time_correction)) {
				return -EFAULT;
			}

			header_ies->time_correction = &header_ie->content.time_correction;
			break;

		case IEEE802154_HEADER_IE_ELEMENT_ID_HEADER_TERMINATION_1:
			/* end of header IEs - continue with payload IEs */
			header_ies->payload_ie_present = true;
			return cursor - start;

		case IEEE802154_HEADER_IE_ELEMENT_ID_HEADER_TERMINATION_2:
			/* end of header IEs - continue with payload */
			header_ies->payload_ie_present = false;
			return cursor - start;

		default:
			/* unsupported information element - ignore... */
			break;
		}

		remaining_length -= progress;
		cursor += progress;

		__ASSERT_NO_MSG(remaining_length >= 0);
	} while (remaining_length > 0);

	/* end of frame */
	return cursor - start;
}

static inline int ieee802154_parse_nested_ies(uint8_t *start, uint8_t remaining_length,
					      struct ieee802154_payload_ies *payload_ies)
{
	struct ieee802154_nested_ie *nested_ie;
	uint8_t *cursor = start;
	uint8_t sub_id;
	int progress;

	if (remaining_length == 0) {
		/* invalid frame - at least one nested IE was expected */
		return -EFAULT;
	}

	do {
		progress = IEEE802154_NESTED_IE_HEADER_LENGTH;

		if (remaining_length < progress) {
			return -EFAULT;
		}

		nested_ie = (struct ieee802154_nested_ie *)cursor;
		remaining_length -= progress;
		cursor += progress;

		if (nested_ie->type == IEEE802154_NESTED_IE_TYPE_SHORT) {
			sub_id = nested_ie->ie_short.sub_id;
			progress = nested_ie->ie_short.length;
		} else if (nested_ie->type == IEEE802154_NESTED_IE_TYPE_LONG) {
			sub_id = nested_ie->ie_long.sub_id;
			progress = ieee802154_nested_ie_long_get_length(
				(struct ieee802154_nested_ie_long *)nested_ie);
		} else {
			return -EINVAL;
		}

		if (remaining_length < progress) {
			return -EFAULT;
		}

		switch (sub_id) {
		case IEEE802154_NESTED_IE_SUB_ID_CHANNEL_HOPPING:
			if (progress ==
			    sizeof(struct ieee802154_nested_ie_channel_hopping_shortened)) {
				payload_ies->hopping_sequence_included = false;
				payload_ies->channel_hopping.channel_hopping_current_hop = 0xffff;
			} else {
				struct ieee802154_nested_ie_channel_hopping_full *content;
				int struct_size;

				content = &nested_ie->content.channel_hopping.full;
				struct_size =
					sizeof(struct ieee802154_nested_ie_channel_hopping_full) +
					content->number_of_channels * sizeof(uint16_t);

				/* The dynamic struct size + two bytes for the following
				 * current hop should be equal to the content size.
				 */
				if (struct_size + sizeof(uint16_t) != progress) {
					/* Either the unsupported extended bitmap is present
					 * or the IE is invalid.
					 */
					return -EFAULT;
				}

				payload_ies->hopping_sequence_included = true;
				payload_ies->channel_hopping.channel_hopping_current_hop =
					*(uint16_t *)(nested_ie + struct_size);
			}

			payload_ies->channel_hopping.content = &nested_ie->content.channel_hopping;
			break;

		case IEEE802154_NESTED_IE_SUB_ID_TSCH_TIMESLOT:
			if (progress == sizeof(struct ieee802154_nested_ie_tsch_timeslot_full)) {
				payload_ies->timeslot_config_included = true;
			} else if (progress ==
				   sizeof(struct ieee802154_nested_ie_tsch_timeslot_shortened)) {
				payload_ies->timeslot_config_included = false;
			} else {
				return -EFAULT;
			}

			payload_ies->tsch_timeslot = &nested_ie->content.tsch_timeslot;
			break;

		case IEEE802154_NESTED_IE_SUB_ID_TSCH_SYNCHRONIZATION:
			if (progress != sizeof(struct ieee802154_nested_ie_tsch_synchronization)) {
				return -EFAULT;
			}

			payload_ies->tsch_synchronization =
				&nested_ie->content.tsch_synchronization;
			break;

		case IEEE802154_NESTED_IE_SUB_ID_TSCH_SLOTFRAME_AND_LINK: {
			struct ieee802154_nested_ie_tsch_slotframe_and_link *content;
			uint8_t struct_size;

			content = &nested_ie->content.tsch_slotframe_and_link;
			struct_size = sizeof(struct ieee802154_nested_ie_tsch_slotframe_and_link);

			if (content->number_of_slotframes >
			    CONFIG_NET_L2_IEEE802154_TSCH_MAX_ADV_SLOTFRAMES) {
				return -ENOMEM;
			}

			payload_ies->tsch_slotframe_and_link.number_of_slotframes =
				content->number_of_slotframes;
			memset(payload_ies->tsch_slotframe_and_link.slotframe_descriptors, 0,
			       sizeof(payload_ies->tsch_slotframe_and_link.slotframe_descriptors));

			for (uint8_t sf_idx = 0; sf_idx < content->number_of_slotframes; sf_idx++) {
				struct ieee802154_slotframe_descriptor *slotframe_descriptor;

				slotframe_descriptor =
					(struct ieee802154_slotframe_descriptor *)(cursor +
										   struct_size);
				struct_size += sizeof(struct ieee802154_slotframe_descriptor);

				if (remaining_length < struct_size) {
					return -EFAULT;
				}

				payload_ies->tsch_slotframe_and_link.slotframe_descriptors[sf_idx] =
					slotframe_descriptor;
				struct_size += slotframe_descriptor->number_of_links *
					       sizeof(struct ieee802154_link_information);

				if (remaining_length < struct_size) {
					return -EFAULT;
				}
			}

			if (progress != struct_size) {
				return -EFAULT;
			}

			break;
		}

		default:
			/* unsupported information element - ignore... */
			break;
		}

		remaining_length -= progress;
		cursor += progress;

		__ASSERT_NO_MSG(remaining_length >= 0);
	} while (remaining_length > 0);

	/* end of nested IEs */
	return cursor - start;
}

int ieee802154_parse_payload_ies(uint8_t *start, uint8_t remaining_length,
				 struct ieee802154_payload_ies *payload_ies)
{
	struct ieee802154_payload_ie *payload_ie;
	uint8_t *cursor = start;
	uint8_t content_length;
	uint8_t group_id;
	int progress;

	if (remaining_length == 0) {
		/* invalid frame - at least one payload IE was expected */
		return -EFAULT;
	}

	do {
		progress = IEEE802154_PAYLOAD_IE_HEADER_LENGTH;

		if (remaining_length < progress) {
			return -EFAULT;
		}

		payload_ie = (struct ieee802154_payload_ie *)cursor;
		remaining_length -= progress;
		cursor += progress;

		if (payload_ie->type != IEEE802154_IE_TYPE_PAYLOAD) {
			return -EINVAL;
		}

		content_length = ieee802154_payload_ie_get_length(payload_ie);

		if (remaining_length < content_length) {
			return -EFAULT;
		}

		group_id = payload_ie->group_id;

		switch (group_id) {
		case IEEE802154_PAYLOAD_IE_GROUP_ID_MLME:
			progress = ieee802154_parse_nested_ies(cursor, content_length, payload_ies);
			if (progress < 0) {
				return progress;
			}
			if (progress != content_length) {
				return -EFAULT;
			}
			break;

		case IEEE802154_PAYLOAD_IE_GROUP_ID_PAYLOAD_TERMINATION:
			/* end of payload IEs - continue with payload */
			return cursor - start;

		default:
			/* unsupported information element - ignore... */
			progress = content_length;
		}

		remaining_length -= progress;
		cursor += progress;

		__ASSERT_NO_MSG(remaining_length >= 0);
	} while (remaining_length > 0);

	/* end of frame */
	return cursor - start;
}

void ieee802154_write_time_correction_header_ie(struct net_buf *frame, bool is_ack,
						int16_t time_correction_us)
{
	struct ieee802154_header_ie *time_correction_ie;
	uint16_t time_sync_info;

	__ASSERT_NO_MSG(time_correction_us > -2048 && time_correction_us < 2047);

	/* Time Correction IE, see section 7.4.2.7 */
	time_correction_ie = (struct ieee802154_header_ie *)net_buf_tail(frame);
	ieee802154_header_ie_set_element_id(time_correction_ie,
					    IEEE802154_HEADER_IE_ELEMENT_ID_TIME_CORRECTION_IE);
	time_correction_ie->length = sizeof(struct ieee802154_header_ie_time_correction);
	time_correction_ie->type = IEEE802154_IE_TYPE_HEADER;
	time_sync_info = (time_correction_us & IEEE802154_HEADER_IE_TIME_CORRECTION_MASK);
	if (!is_ack) {
		time_sync_info |= IEEE802154_HEADER_IE_TIME_CORRECTION_NACK;
	}
	time_correction_ie->content.time_correction.time_sync_info =
		sys_cpu_to_le16(time_sync_info);
	net_buf_add(frame, IEEE802154_HEADER_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_header_ie_time_correction));
}

void ieee802154_write_header_termination_1_header_ie(struct net_buf *frame)
{
	struct ieee802154_header_ie *header_termination1_ie;

	/* Header Termination IE, see sections 7.4.1 and 7.4.2.18 */
	header_termination1_ie = (struct ieee802154_header_ie *)net_buf_tail(frame);
	ieee802154_header_ie_set_element_id(header_termination1_ie,
					    IEEE802154_HEADER_IE_ELEMENT_ID_HEADER_TERMINATION_1);
	header_termination1_ie->length = 0;
	header_termination1_ie->type = IEEE802154_IE_TYPE_HEADER;
	net_buf_add(frame, IEEE802154_HEADER_IE_HEADER_LENGTH);
}

void ieee802154_write_mlme_payload_ie_header(struct net_buf *frame, uint16_t content_length)
{
	struct ieee802154_payload_ie *mlme_ie;

	/* MLME Payload IE, see section 7.4.3.3 */
	mlme_ie = (struct ieee802154_payload_ie *)net_buf_tail(frame);
	ieee802154_payload_ie_set_length(mlme_ie, content_length);
	mlme_ie->group_id = IEEE802154_PAYLOAD_IE_GROUP_ID_MLME;
	mlme_ie->type = IEEE802154_IE_TYPE_PAYLOAD;
	net_buf_add(frame, IEEE802154_PAYLOAD_IE_HEADER_LENGTH);
}

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
/* context must be locked */
void ieee802154_write_tsch_synchronization_nested_ie(struct net_buf *frame,
						     struct ieee802154_context *ctx)
{
	struct ieee802154_nested_ie *nested_ie;

	__ASSERT_NO_MSG((ctx->tsch_asn & 0xffffffffff) == ctx->tsch_asn);

	/* see section 6.3.6: "TSCH Synchronization IE, as described in 7.4.4.2, containing
	 * timing information so new devices can synchronize to the network."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_SHORT;
	nested_ie->ie_short.sub_id = IEEE802154_NESTED_IE_SUB_ID_TSCH_SYNCHRONIZATION;
	nested_ie->ie_short.length = sizeof(struct ieee802154_nested_ie_tsch_synchronization);
	nested_ie->content.tsch_synchronization.join_metric = ctx->tsch_join_metric;
	nested_ie->content.tsch_synchronization.asn[0] = ctx->tsch_asn;
	nested_ie->content.tsch_synchronization.asn[1] = ctx->tsch_asn >> 8U;
	nested_ie->content.tsch_synchronization.asn[2] = ctx->tsch_asn >> 16U;
	nested_ie->content.tsch_synchronization.asn[3] = ctx->tsch_asn >> 24U;
	nested_ie->content.tsch_synchronization.asn[4] = ctx->tsch_asn >> 32U;
	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_nested_ie_tsch_synchronization));
}

/* context must be locked */
void ieee802154_write_full_tsch_timeslot_nested_ie(struct net_buf *frame,
						   struct ieee802154_context *ctx)
{
	struct ieee802154_tsch_timeslot_template *tsch_timeslot_template;
	struct ieee802154_nested_ie_tsch_timeslot_full *timeslot_ie;
	struct ieee802154_nested_ie *nested_ie;

	/* see section 6.3.6: "TSCH Timeslot IE, as described in 7.4.4.4, containing timeslot
	 * information describing when to expect a frame to be transmitted and when to send an
	 * acknowledgment."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_SHORT;
	nested_ie->ie_short.sub_id = IEEE802154_NESTED_IE_SUB_ID_TSCH_TIMESLOT;
	nested_ie->ie_short.length = sizeof(struct ieee802154_nested_ie_tsch_timeslot_full);

	tsch_timeslot_template = &ctx->tsch_timeslot_template;
	timeslot_ie = &nested_ie->content.tsch_timeslot.full;

	*timeslot_ie = (struct ieee802154_nested_ie_tsch_timeslot_full){
		.timeslot_id = 0,
		.cca_offset = sys_cpu_to_le16(tsch_timeslot_template->cca_offset),
		.cca = sys_cpu_to_le16(tsch_timeslot_template->cca),
		.tx_offset = sys_cpu_to_le16(tsch_timeslot_template->tx_offset),
		.rx_offset = sys_cpu_to_le16(tsch_timeslot_template->rx_offset),
		.rx_ack_delay = sys_cpu_to_le16(tsch_timeslot_template->rx_ack_delay),
		.tx_ack_delay = sys_cpu_to_le16(tsch_timeslot_template->tx_ack_delay),
		.rx_wait = sys_cpu_to_le16(tsch_timeslot_template->rx_wait),
		.ack_wait = sys_cpu_to_le16(tsch_timeslot_template->ack_wait),
		.rx_tx = sys_cpu_to_le16(tsch_timeslot_template->rx_tx),
		.max_ack = sys_cpu_to_le16(tsch_timeslot_template->max_ack),
	};

	ieee802154_nested_ie_tsch_timeslot_full_set_max_tx(timeslot_ie,
							   tsch_timeslot_template->max_tx);
	ieee802154_nested_ie_tsch_timeslot_full_set_timeslot_length(timeslot_ie,
								    tsch_timeslot_template->length);

	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_nested_ie_tsch_timeslot_full));
}

/* context must be locked */
void ieee802154_write_shortened_tsch_timeslot_nested_ie(struct net_buf *frame)
{
	struct ieee802154_nested_ie *nested_ie;

	/* see section 6.3.6: "TSCH Timeslot IE, as described in 7.4.4.4, containing timeslot
	 * information describing when to expect a frame to be transmitted and when to send an
	 * acknowledgment."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_SHORT;
	nested_ie->ie_short.sub_id = IEEE802154_NESTED_IE_SUB_ID_TSCH_TIMESLOT;
	nested_ie->ie_short.length = sizeof(struct ieee802154_nested_ie_tsch_timeslot_shortened);
	nested_ie->content.tsch_timeslot.shortened.timeslot_id = 0;
	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_nested_ie_tsch_timeslot_shortened));
}

/* context must be locked */
void ieee802154_write_tsch_slotframe_and_link_nested_ie(struct net_buf *frame,
							struct ieee802154_context *ctx)
{
	struct ieee802154_slotframe_descriptor *slotframe_descriptor;
	struct ieee802154_tsch_slotframe *slotframe;
	struct ieee802154_nested_ie *nested_ie;
	int num_advertised_slotframes = 0;
	int num_advertised_links = 0;

	/* see section 6.3.6: "TSCH Slotframe and Link IE, as described in 7.4.4.3, containing
	 * initial link and slotframe information so new devices know when to listen for
	 * transmissions from the advertising device and when they can transmit to the
	 * advertising device."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_SHORT;
	nested_ie->ie_short.sub_id = IEEE802154_NESTED_IE_SUB_ID_TSCH_SLOTFRAME_AND_LINK;
	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_nested_ie_tsch_slotframe_and_link));

	SYS_SFLIST_FOR_EACH_CONTAINER(&ctx->tsch_slotframe_table, slotframe, sfnode) {
		int num_advertised_links_in_slotframe;
		struct ieee802154_tsch_link *link;

		if (!slotframe->advertise) {
			continue;
		}

		num_advertised_slotframes++;
		num_advertised_links_in_slotframe = 0;

		slotframe_descriptor =
			(struct ieee802154_slotframe_descriptor *)net_buf_tail(frame);
		slotframe_descriptor->slotframe_handle = slotframe->handle;
		slotframe_descriptor->slotframe_size = sys_cpu_to_le16(slotframe->size);

		SYS_SFLIST_FOR_EACH_CONTAINER(&slotframe->link_table, link, sfnode) {

			if (!link->advertise) {
				continue;
			}

			slotframe_descriptor
				->link_information_fields[num_advertised_links_in_slotframe] =
				(struct ieee802154_link_information){
					.timeslot = sys_cpu_to_le16(link->timeslot),
					.channel_offset = sys_cpu_to_le16(link->channel_offset),
					.tx_link = link->tx,
					.rx_link = link->rx,
					.shared_link = link->shared,
					.timekeeping = link->timekeeping,
					.priority = link->priority,
				};

			num_advertised_links_in_slotframe++;
			num_advertised_links++;
		}

		slotframe_descriptor->number_of_links = num_advertised_links_in_slotframe;

		net_buf_add(frame, sizeof(struct ieee802154_slotframe_descriptor) +
					   num_advertised_links_in_slotframe *
						   sizeof(struct ieee802154_link_information));
	}

	nested_ie->ie_short.length =
		sizeof(struct ieee802154_nested_ie_tsch_slotframe_and_link) +
		num_advertised_slotframes * sizeof(struct ieee802154_slotframe_descriptor) +
		num_advertised_links * sizeof(struct ieee802154_link_information);
	nested_ie->content.tsch_slotframe_and_link.number_of_slotframes = num_advertised_slotframes;
}
#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

/* context must be locked */
void ieee802154_write_full_channel_hopping_nested_ie(struct net_buf *frame, struct net_if *iface,
						     uint16_t current_hop)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	struct ieee802154_nested_ie *nested_ie;
	uint16_t hopping_sequence_length;
	uint32_t phy_configuration = 0;
	uint16_t content_length;

	hopping_sequence_length = IEEE802154_HOPPING_SEQUENCE_LENGTH(ctx);

	if (hopping_sequence_length == 0) {
		return;
	}

	/* see section 6.3.6: "Channel hopping IE, as described in 7.4.4.31, containing
	 * channel hopping information, as described in 6.2.10."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_LONG;
	nested_ie->ie_long.sub_id = IEEE802154_NESTED_IE_SUB_ID_CHANNEL_HOPPING;

	content_length = sizeof(struct ieee802154_nested_ie_channel_hopping_full) +
			 (hopping_sequence_length + 1) * sizeof(uint16_t);
	ieee802154_nested_ie_long_set_length(&nested_ie->ie_long, content_length);

	/* Currently we only support the legacy channel page (zero). */
	nested_ie->content.channel_hopping.full =
		(struct ieee802154_nested_ie_channel_hopping_full){
			.hopping_sequence_id = 0,
			.channel_page = 0,
			.number_of_channels =
				sys_cpu_to_le16(ieee802154_radio_number_of_channels(iface)),
			.hopping_sequence_length = sys_cpu_to_le16(hopping_sequence_length),
		};

	for (int i = 0; i < hopping_sequence_length; i++) {
		uint16_t channel = IEEE802154_HOPPING_SEQUENCE_ENTRY(ctx, i);

		__ASSERT_NO_MSG(ieee802154_radio_verify_channel(iface, channel));

		phy_configuration |= (1U << channel);
		nested_ie->content.channel_hopping.full.hopping_sequence[i] =
			sys_cpu_to_le16(channel);
	}

	nested_ie->content.channel_hopping.full.phy_configuration =
		sys_cpu_to_le32(phy_configuration);
	ieee802154_nested_ie_channel_hopping_full_set_current_hop(
		&nested_ie->content.channel_hopping.full, current_hop);

	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH + content_length);
}

/* context must be locked */
void ieee802154_write_shortened_channel_hopping_nested_ie(struct net_buf *frame)
{
	struct ieee802154_nested_ie *nested_ie;

	/* see section 6.3.6: "Channel hopping IE, as described in 7.4.4.31, containing
	 * channel hopping information, as described in 6.2.10."
	 */
	nested_ie = (struct ieee802154_nested_ie *)net_buf_tail(frame);
	nested_ie->type = IEEE802154_NESTED_IE_TYPE_LONG;
	nested_ie->ie_long.sub_id = IEEE802154_NESTED_IE_SUB_ID_CHANNEL_HOPPING;
	ieee802154_nested_ie_long_set_length(
		&nested_ie->ie_long, sizeof(struct ieee802154_nested_ie_channel_hopping_shortened));
	nested_ie->content.channel_hopping.shortened.hopping_sequence_id = 0;
	net_buf_add(frame, IEEE802154_NESTED_IE_HEADER_LENGTH +
				   sizeof(struct ieee802154_nested_ie_channel_hopping_shortened));
}
