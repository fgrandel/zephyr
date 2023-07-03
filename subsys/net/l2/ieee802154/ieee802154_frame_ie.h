/*
 * Copyright (c) 2022 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 MAC frame information element (IE) related functions
 *
 * This is not to be included by the application.
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 *
 * @note All structs and attributes in this file that directly represent parts
 * of IEEE 802.15.4 frames are in LITTLE ENDIAN, see section 4, especially
 * section 4.3.
 */

#ifndef __IEEE802154_FRAME_IE_H__
#define __IEEE802154_FRAME_IE_H__

#include <zephyr/device.h>
#include <zephyr/net/buf.h>
#include <zephyr/net/ieee802154.h>
#include <zephyr/net/ieee802154_ie.h>

/* see section 7.4.4.2 */
struct ieee802154_nested_ie_tsch_synchronization {
	uint8_t asn[5];
	uint8_t join_metric;
} __packed;

/* see section 7.4.4.3, figure 7-54 */
struct ieee802154_link_information {
	uint16_t timeslot;
	uint16_t channel_offset;
#if CONFIG_LITTLE_ENDIAN
	uint8_t tx_link : 1;
	uint8_t rx_link : 1;
	uint8_t shared_link : 1;
	uint8_t timekeeping : 1;
	uint8_t priority : 1;
	uint8_t reserved : 3;
#else
	uint8_t reserved : 3;
	uint8_t priority : 1;
	uint8_t timekeeping : 1;
	uint8_t shared_link : 1;
	uint8_t rx_link : 1;
	uint8_t tx_link : 1;
#endif
} __packed;

/* see section 7.4.4.3, figure 7-53 */
struct ieee802154_slotframe_descriptor {
	uint8_t slotframe_handle;
	uint16_t slotframe_size;
	uint8_t number_of_links;
	struct ieee802154_link_information link_information_fields[0];
} __packed;

/* see section 7.4.4.3, figure 7-52 */
struct ieee802154_nested_ie_tsch_slotframe_and_link {
	uint8_t number_of_slotframes;
	/* A variable-length list of variable size slotframe descriptors follows */
} __packed;

/* see section 7.4.4.4 */
struct ieee802154_nested_ie_tsch_timeslot_shortened {
	uint8_t timeslot_id;
} __packed;

/* see section 7.4.4.4 */
struct ieee802154_nested_ie_tsch_timeslot_full {
	uint8_t timeslot_id;
	uint16_t cca_offset;
	uint16_t cca;
	uint16_t tx_offset;
	uint16_t rx_offset;
	uint16_t rx_ack_delay;
	uint16_t tx_ack_delay;
	uint16_t rx_wait;
	uint16_t ack_wait;
	uint16_t rx_tx;
	uint16_t max_ack;
	uint8_t max_tx[3];
	uint8_t timeslot_length[3];
} __packed;

/**
 * Set the maxTx metric from a timeslot IE.
 *
 * @param ie pointer to a timeslot IE
 * @param max_tx Max TX in CPU byte order
 */
static inline void ieee802154_nested_ie_tsch_timeslot_full_set_max_tx(
	struct ieee802154_nested_ie_tsch_timeslot_full *ie, uint32_t max_tx)
{
	ie->max_tx[0] = max_tx;
	ie->max_tx[1] = max_tx >> 8U;
	ie->max_tx[2] = max_tx >> 16U;
}

/**
 * Get the maxTx metric from a timeslot IE.
 *
 * @param ie pointer to a timeslot IE
 *
 * @return timeslot max TX in CPU byte order
 */
static inline uint32_t ieee802154_nested_ie_tsch_timeslot_full_get_max_tx(
	struct ieee802154_nested_ie_tsch_timeslot_full *ie)
{
	return ie->max_tx[0] | (ie->max_tx[1] << 8U) | (ie->max_tx[2] << 16U);
}

/**
 * Set the timeslot length from a timeslot IE.
 *
 * @param ie pointer to a timeslot IE
 * @param timeslot_length Timeslot length in CPU byte order
 */
static inline void ieee802154_nested_ie_tsch_timeslot_full_set_timeslot_length(
	struct ieee802154_nested_ie_tsch_timeslot_full *ie, uint32_t timeslot_length)
{
	ie->timeslot_length[0] = timeslot_length;
	ie->timeslot_length[1] = timeslot_length >> 8U;
	ie->timeslot_length[2] = timeslot_length >> 16U;
}

/**
 * Get the timeslot length from a timeslot IE.
 *
 * @return timeslot length in CPU byte order
 */
static inline uint32_t ieee802154_nested_ie_tsch_timeslot_full_get_timeslot_length(
	struct ieee802154_nested_ie_tsch_timeslot_full *ie)
{
	return ie->timeslot_length[0] | (ie->timeslot_length[1] << 8U) |
	       (ie->timeslot_length[2] << 16U);
}

/* see section 7.4.4.4 */
struct ieee802154_nested_ie_tsch_timeslot {
	union {
		struct ieee802154_nested_ie_tsch_timeslot_shortened shortened;
		struct ieee802154_nested_ie_tsch_timeslot_full full;
	};
} __packed;

/* see section 7.4.4.31 */
struct ieee802154_nested_ie_channel_hopping_shortened {
	uint8_t hopping_sequence_id;
} __packed;

/* see section 7.4.4.31 */
struct ieee802154_nested_ie_channel_hopping_full {
	uint8_t hopping_sequence_id;
	uint8_t channel_page;
	uint16_t number_of_channels;
	uint32_t phy_configuration;
	/* An underspecified optional "extended bitmap" follows
	 * - not supported as I don't have access to the WiSUN spec
	 * where this is probably better explained.
	 */
	uint16_t hopping_sequence_length;
	uint16_t hopping_sequence[0];
	/* Followed by the current_hop (16 bit unsigned integer)
	 * which cannot be placed in the struct but will be
	 * extracted and made available during IE parsing.
	 */
} __packed;

/**
 * Set the current hop of a channel hopping IE
 *
 * Requires the hopping sequence length to be set in the IE.
 *
 * @param ie Pointer to a full channel hopping IE
 * @param current_hop current hop in CPU byte order
 */
static inline void ieee802154_nested_ie_channel_hopping_full_set_current_hop(
	struct ieee802154_nested_ie_channel_hopping_full *ie, uint16_t current_hop)
{
	uint16_t hopping_sequence_length = sys_le16_to_cpu(ie->hopping_sequence_length);

	ie->hopping_sequence[hopping_sequence_length] = sys_cpu_to_le16(current_hop);
}

/**
 * Get the current hop of a channel hopping IE
 *
 * @param ie Pointer to a full channel hopping IE
 * @return current hop in CPU byte order
 */
static inline uint16_t ieee802154_nested_ie_channel_hopping_full_get_current_hop(
	struct ieee802154_nested_ie_channel_hopping_full *ie)
{
	uint16_t hopping_sequence_length = sys_le16_to_cpu(ie->hopping_sequence_length);

	return sys_le16_to_cpu(ie->hopping_sequence[hopping_sequence_length]);
}

/* see section 7.4.4.31 */
struct ieee802154_nested_ie_channel_hopping {
	union {
		struct ieee802154_nested_ie_channel_hopping_shortened shortened;
		struct ieee802154_nested_ie_channel_hopping_full full;
	};
} __packed;

/* see section 7.4.4.1, tables 7-18 and 7-19,
 * We use a single list as sub-ids of short and long
 * nested IEs share a common number range.
 */
enum ieee802154_nested_ie_sub_id {
	/* long nested IEs */
	IEEE802154_NESTED_IE_SUB_ID_CHANNEL_HOPPING = 0x9,
	/* short nested IEs */
	IEEE802154_NESTED_IE_SUB_ID_TSCH_SYNCHRONIZATION = 0x1a,
	IEEE802154_NESTED_IE_SUB_ID_TSCH_SLOTFRAME_AND_LINK,
	IEEE802154_NESTED_IE_SUB_ID_TSCH_TIMESLOT,
	/* partial list, add additional ids as needed */
};

/* see section 7.4.4.1 */
struct ieee802154_nested_ie_short {
#if CONFIG_LITTLE_ENDIAN
	uint16_t length : 8;
	uint16_t sub_id : 7; /* see enum ieee802154_nested_ie_sub_id */
	uint16_t reserved : 1;
#else
	uint16_t length : 8;
	uint16_t reserved : 1;
	uint16_t sub_id : 7;
#endif
} __packed;

/* see section 7.4.4.1 */
struct ieee802154_nested_ie_long {
#if CONFIG_LITTLE_ENDIAN
	uint16_t length_low : 8;
	uint16_t length_high : 3;
	uint16_t sub_id : 4; /* see enum ieee802154_nested_ie_sub_id */
	uint16_t reserved : 1;
#else
	uint16_t length_low : 8;
	uint16_t reserved : 1;
	uint16_t sub_id : 4;
	uint16_t length_high : 3;
#endif
} __packed;

#define IEEE802154_NESTED_IE_HEADER_LENGTH sizeof(uint16_t)

/**
 * Set the long nested IE length
 *
 * @param ie long nested IE
 * @param length IE length in CPU byte order
 */
static inline void ieee802154_nested_ie_long_set_length(struct ieee802154_nested_ie_long *ie,
							uint16_t length)
{
	ie->length_high = length >> 8U;
	ie->length_low = length & 0x00ff;
}

/**
 * Get the long nested IE length
 *
 * @return long nested IE length in CPU byte order
 */
static inline uint16_t ieee802154_nested_ie_long_get_length(struct ieee802154_nested_ie_long *ie)
{
	return (ie->length_high << 8U) | ie->length_low;
}

/* see section 7.4.4.1 */
enum ieee802154_nested_ie_type {
	IEEE802154_NESTED_IE_TYPE_SHORT = 0x0,
	IEEE802154_NESTED_IE_TYPE_LONG,
};

/* see section 7.4.4.1 */
struct ieee802154_nested_ie {
	union {
		/* initial distinction between long and short IE type,
		 * see enum ieee802154_nested_ie_type
		 */
		struct {
#if CONFIG_LITTLE_ENDIAN
			uint16_t sub_id_and_length : 15; /* can only be parsed after
							  * reading the type field
							  */
			uint16_t type : 1; /* 0 = short nested ie, 1 = long nested ie */
#else
			uint16_t sub_id_and_length : 8;
			uint16_t type : 1; /* 0 = short nested ie, 1 = long nested ie */
			uint16_t sub_id_and_length : 7;
#endif
		};
		/* long and short IE common fields */
		struct ieee802154_nested_ie_short ie_short;
		struct ieee802154_nested_ie_long ie_long;
	};
	union {
		struct ieee802154_nested_ie_tsch_synchronization tsch_synchronization;
		struct ieee802154_nested_ie_tsch_slotframe_and_link tsch_slotframe_and_link;
		struct ieee802154_nested_ie_tsch_timeslot tsch_timeslot;
		struct ieee802154_nested_ie_channel_hopping channel_hopping;
		/* May be extended with further IEs as needed. */
	} content;
} __packed;

/**
 * @return Nested IE length in CPU byte order.
 */
static inline uint16_t ieee802154_nested_ie_length(struct ieee802154_nested_ie *ie)
{
	return (ie->type == IEEE802154_NESTED_IE_TYPE_SHORT)
		       ? ((struct ieee802154_nested_ie_short *)ie)->length
		       : ieee802154_nested_ie_long_get_length(
				 (struct ieee802154_nested_ie_long *)ie);
}

/**
 * @return Nested IE sub id in CPU byte order.
 */
static inline uint16_t ieee802154_nested_ie_sub_id(struct ieee802154_nested_ie *ie)
{
	return ie->type == IEEE802154_NESTED_IE_TYPE_SHORT
		       ? ((struct ieee802154_nested_ie_short *)ie)->sub_id
		       : ((struct ieee802154_nested_ie_long *)ie)->sub_id;
}

/* see section 7.4.3.1, table 7-17,
 * partial list, only IE groups actually used are implemented
 */
enum ieee802154_payload_ie_group_id {
	IEEE802154_PAYLOAD_IE_GROUP_ID_MLME = 0x1,
	IEEE802154_PAYLOAD_IE_GROUP_ID_PAYLOAD_TERMINATION = 0xf
	/* partial list, add additional ids as needed */
};

/* see section 7.4.3.1 */
struct ieee802154_payload_ie {
#if CONFIG_LITTLE_ENDIAN
	uint16_t length_low : 8;
	uint16_t length_high : 3;
	uint16_t group_id : 4; /* see enum ieee802154_payload_ie_group_id */
	uint16_t type : 1;     /* always 1 */
#else
	uint16_t length_low : 8;
	uint16_t type : 1;     /* always 1 */
	uint16_t group_id : 4; /* see enum ieee802154_payload_ie_group_id */
	uint16_t length_high : 3;
#endif
	/* Currently we support variable-element-length list (MLME/
	 * nested IE) and zero-content (termination) payload IEs -
	 * both cannot be structured any further.
	 */
} __packed;

#define IEEE802154_PAYLOAD_IE_HEADER_LENGTH sizeof(uint16_t)

/**
 * Set the payload IE length
 *
 * @param ie pointer to a payload IE
 * @param length IE length in CPU byte order
 */
static inline void ieee802154_payload_ie_set_length(struct ieee802154_payload_ie *ie,
						    uint16_t length)
{
	ie->length_high = length >> 8U;
	ie->length_low = length & 0x00ff;
}

/**
 * Get the payload IE length
 *
 * @param ie pointer to a payload IE
 *
 * @return payload IE length in CPU byte order
 */
static inline uint16_t ieee802154_payload_ie_get_length(struct ieee802154_payload_ie *ie)
{
	return (ie->length_high << 8U) | ie->length_low;
}

/**
 * Parsed payload IEs
 *
 * Pointers into the frame will be the main means to present
 * parsed IEs.
 *
 * Where different versions of IEs exist, the version is indicated
 * in the flag bitmap. Where the variability of the IE is such that
 * it cannot be represented by a boolean flag alone or where usage
 * of the structure can be simplified, specific information is
 * extracted from the frame during parsing (e.g. the number of
 * slotframes in the TSCH Slotframe and Link IE or the current
 * hop from the Channel Hopping IE).
 */
struct ieee802154_payload_ies {
	struct ieee802154_nested_ie_tsch_synchronization *tsch_synchronization;
	struct {
		struct ieee802154_slotframe_descriptor
			*slotframe_descriptors[CONFIG_NET_L2_IEEE802154_TSCH_MAX_ADV_SLOTFRAMES];
		uint8_t number_of_slotframes; /* zero if no TSCH slotframe
					       * and link IE was present
					       */
	} tsch_slotframe_and_link;
	struct ieee802154_nested_ie_tsch_timeslot *tsch_timeslot;
	struct {
		struct ieee802154_nested_ie_channel_hopping *content;
		uint16_t channel_hopping_current_hop; /* little endian,
						       * 0xffff if not present in the IE
						       */
	} channel_hopping;
	uint32_t full_tsch_timeslot : 1;
	uint32_t timeslot_config_included : 1;	/* full TSCH timeslot config in IE */
	uint32_t hopping_sequence_included : 1; /* full hopping sequence in IE */
	uint32_t _unused : 29;
};

/**
 * Parse Payload Information Elements.
 *
 * @param start Pointer to the first byte in a buffer that encodes Payload IEs.
 * @param remaining_length Number of (unparsed) bytes in the buffer.
 * @param payload_ies Pointer to the structure that will contain the parsed Payload IEs.
 */
int ieee802154_parse_payload_ies(uint8_t *start, uint8_t remaining_length,
				 struct ieee802154_payload_ies *payload_ies);

#define IEEE802154_MLME_PAYLOAD_IE_LEN(nested_ies_len)                                             \
	(IEEE802154_PAYLOAD_IE_HEADER_LENGTH + nested_ies_len)

/**
 * Write an "MLME" Payload IE to the given buffer.
 */
void ieee802154_write_mlme_payload_ie_header(struct net_buf *frame, uint16_t content_length);

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
#define IEEE802154_TSCH_SYNCHRONIZATION_NESTED_IE_LEN                                              \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_tsch_synchronization))

/**
 * Write a "TSCH Synchronization" Nested IE to the given buffer.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_tsch_synchronization_nested_ie(struct net_buf *frame,
						     struct ieee802154_context *ctx);

#define IEEE802154_FULL_TSCH_TIMESLOT_NESTED_IE_LEN                                                \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_tsch_timeslot_full))

/**
 * Write a full "TSCH Timeslot" Nested IE to the given buffer including all
 * timing information.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_full_tsch_timeslot_nested_ie(struct net_buf *frame,
						   struct ieee802154_context *ctx);

#define IEEE802154_SHORTENED_TSCH_TIMESLOT_NESTED_IE_LEN                                           \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_tsch_timeslot_shortened))

/**
 * Write a shortened "TSCH Timeslot" Nested IE to the given buffer that only
 * contains the timeslot template id.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_shortened_tsch_timeslot_nested_ie(struct net_buf *frame);

#define IEEE802154_TSCH_SLOTFRAME_AND_LINK_NESTED_IE_LEN(num_sf, num_links)                        \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_tsch_slotframe_and_link) +                             \
	 (num_sf) * sizeof(struct ieee802154_slotframe_descriptor) +                               \
	 (num_links) * sizeof(struct ieee802154_link_information))

/**
 * Write a "TSCH Slotframe and Link" Nested IE to the given buffer.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_tsch_slotframe_and_link_nested_ie(struct net_buf *frame,
							struct ieee802154_context *ctx);
#else

#define IEEE802154_TSCH_SYNCHRONIZATION_NESTED_IE_LEN                       0
#define IEEE802154_FULL_TSCH_TIMESLOT_NESTED_IE_LEN                         0
#define IEEE802154_SHORTENED_TSCH_TIMESLOT_NESTED_IE_LEN                    0
#define IEEE802154_TSCH_SLOTFRAME_AND_LINK_NESTED_IE_LEN(num_sf, num_links) 0

#define ieee802154_write_tsch_synchronization_nested_ie(...)
#define ieee802154_write_full_tsch_timeslot_nested_ie(...)
#define ieee802154_write_shortened_tsch_timeslot_nested_ie(...)
#define ieee802154_write_tsch_slotframe_and_link_nested_ie(...)

#endif /* CONFIG_NET_L2_IEEE802154_TSCH */

#define IEEE802154_FULL_CHANNEL_HOPPING_NESTED_IE_LEN(hopping_sequence_length)                     \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_channel_hopping_full) +                                \
	 ((hopping_sequence_length) + 1) * sizeof(uint16_t))

/**
 * Write a full "Channel Hopping" Nested IE to the given buffer including
 * all channel hopping sequence details.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_full_channel_hopping_nested_ie(struct net_buf *frame, struct net_if *iface,
						     uint16_t current_hop);

#define IEEE802154_SHORTENED_CHANNEL_HOPPING_NESTED_IE_LEN                                         \
	(IEEE802154_NESTED_IE_HEADER_LENGTH +                                                      \
	 sizeof(struct ieee802154_nested_ie_channel_hopping_shortened))

/**
 * Write a shortened "Channel Hopping" Nested IE to the given
 * buffer that only contains the channel hopping sequence id.
 *
 * The given context must be locked while calling this method.
 */
void ieee802154_write_shortened_channel_hopping_nested_ie(struct net_buf *frame);
#endif /* __IEEE802154_FRAME_IE_H__ */
