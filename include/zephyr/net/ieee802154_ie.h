/*
 * Copyright (c) 2022 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 MAC information element (IE) related types and helpers
 *
 * This is not to be included by the application. This file contains only those
 * parts of the types required for IE support that need to be visible to IEEE
 * 802.15.4 drivers and L2 at the same time, i.e. everything related to header
 * IE representation, parsing and generation. Types visible to L2 only can be
 * found in the subsystem folder in ieee802154_frame_ie.h
 *
 * All specification references in this file refer to IEEE 802.15.4-2020.
 *
 * @note All structs and attributes in this file that directly represent parts
 * of IEEE 802.15.4 frames are in LITTLE ENDIAN, see section 4, especially
 * section 4.3.
 */

#ifndef __IEEE802154_IE_H__
#define __IEEE802154_IE_H__

#include <zephyr/net/buf.h>
#include <zephyr/sys/byteorder.h>

/**
 * @addtogroup ieee802154_driver
 * @{
 *
 * @name MAC header information elements (section 7.4.2)
 * @{
 */

/**
 * Information Element Types
 *
 * see sections 7.4.2.1 and 7.4.3.1
 */
enum ieee802154_ie_type {
	IEEE802154_IE_TYPE_HEADER = 0x0,
	IEEE802154_IE_TYPE_PAYLOAD,
};

/**
 * Header Information Element IDs
 *
 * see section 7.4.2.1, table 7-7, partial list, only IEs actually used are
 * implemented
 */
enum ieee802154_header_ie_element_id {
	IEEE802154_HEADER_IE_ELEMENT_ID_VENDOR_SPECIFIC_IE = 0x00,
	IEEE802154_HEADER_IE_ELEMENT_ID_CSL_IE = 0x1a,
	IEEE802154_HEADER_IE_ELEMENT_ID_RIT_IE = 0x1b,
	IEEE802154_HEADER_IE_ELEMENT_ID_RENDEZVOUS_TIME_IE = 0x1d,
	IEEE802154_HEADER_IE_ELEMENT_ID_TIME_CORRECTION_IE = 0x1e,
	IEEE802154_HEADER_IE_ELEMENT_ID_HEADER_TERMINATION_1 = 0x7e,
	IEEE802154_HEADER_IE_ELEMENT_ID_HEADER_TERMINATION_2 = 0x7f,
	/* partial list, add additional ids as needed */
};

/** @cond INTERNAL_HIDDEN */
#define IEEE802154_VENDOR_SPECIFIC_IE_OUI_LEN 3
/** INTERNAL_HIDDEN @endcond */

/** Vendor Specific Header IE, see section 7.4.2.3 */
struct ieee802154_header_ie_vendor_specific {
	uint8_t vendor_oui[IEEE802154_VENDOR_SPECIFIC_IE_OUI_LEN];
	uint8_t *vendor_specific_info;
} __packed;

/** Full CSL IE, see section 7.4.2.3 */
struct ieee802154_header_ie_csl_full {
	uint16_t csl_phase;
	uint16_t csl_period;
	uint16_t csl_rendezvous_time;
} __packed;

/** Reduced CSL IE, see section 7.4.2.3 */
struct ieee802154_header_ie_csl_reduced {
	uint16_t csl_phase;
	uint16_t csl_period;
} __packed;

/** Generic CSL IE, see section 7.4.2.3 */
struct ieee802154_header_ie_csl {
	union {
		struct ieee802154_header_ie_csl_reduced reduced;
		struct ieee802154_header_ie_csl_full full;
	};
} __packed;

/** RIT IE, see section 7.4.2.4 */
struct ieee802154_header_ie_rit {
	uint8_t time_to_first_listen;
	uint8_t number_of_repeat_listen;
	uint16_t repeat_listen_interval;
} __packed;

/** Rendezvous Time IE, see section 7.4.2.6 */
struct ieee802154_header_ie_rendezvous_time {
	uint16_t rendezvous_time;
	uint16_t wakeup_interval;
} __packed;

/** Time Correction IE, see section 7.4.2.7 */
struct ieee802154_header_ie_time_correction {
	uint16_t time_sync_info;
} __packed;

/* Generic Header IE, see section 7.4.2.1 */
struct ieee802154_header_ie {
#if CONFIG_LITTLE_ENDIAN
	uint16_t length : 7;
	uint16_t element_id_low : 1; /* see enum ieee802154_header_ie_element_id */
	uint16_t element_id_high : 7;
	uint16_t type : 1; /* always 0 */
#else
	uint16_t element_id_low : 1; /* see enum ieee802154_header_ie_element_id */
	uint16_t length : 7;
	uint16_t type : 1; /* always 0 */
	uint16_t element_id_high : 7;
#endif
	union {
		struct ieee802154_header_ie_vendor_specific vendor_specific;
		struct ieee802154_header_ie_csl csl;
		struct ieee802154_header_ie_rit rit;
		struct ieee802154_header_ie_rendezvous_time rendezvous_time;
		struct ieee802154_header_ie_time_correction time_correction;
		/* add additional supported header IEs here */
	} content;
} __packed;

/** The header IE's header length (2 bytes) */
#define IEEE802154_HEADER_IE_HEADER_LENGTH sizeof(uint16_t)


/** @cond INTERNAL_HIDDEN */
#define IEEE802154_DEFINE_HEADER_IE(_element_id, _length, _content, _content_type)                 \
	(struct ieee802154_header_ie) {                                                            \
		.length = (_length),                                                               \
		.element_id_high = (_element_id) >> 1U, .element_id_low = (_element_id) & 0x01,    \
		.type = IEEE802154_IE_TYPE_HEADER,                                                 \
		.content._content_type = _content,                                                 \
	}

#define IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC_CONTENT_LEN(_vendor_specific_info_len)         \
	(IEEE802154_VENDOR_SPECIFIC_IE_OUI_LEN + (_vendor_specific_info_len))

#define IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC_CONTENT(_vendor_oui, _vendor_specific_info)    \
	(struct ieee802154_header_ie_vendor_specific) {                                            \
		.vendor_oui = _vendor_oui, .vendor_specific_info = (_vendor_specific_info),        \
	}

#define IEEE802154_DEFINE_HEADER_IE_CSL_REDUCED_CONTENT(_csl_phase, _csl_period)                   \
	(struct ieee802154_header_ie_csl_reduced) {                                                \
		.csl_phase = sys_cpu_to_le16(_csl_phase),                                          \
		.csl_period = sys_cpu_to_le16(_csl_period),                                        \
	}

#define IEEE802154_DEFINE_HEADER_IE_CSL_FULL_CONTENT(_csl_phase, _csl_period,                      \
						     _csl_rendezvous_time)                         \
	(struct ieee802154_header_ie_csl_full) {                                                   \
		.csl_phase = sys_cpu_to_le16(_csl_phase),                                          \
		.csl_period = sys_cpu_to_le16(_csl_period),                                        \
		.csl_rendezvous_time = sys_cpu_to_le16(_csl_rendezvous_time),                      \
	}

#define IEEE802154_HEADER_IE_TIME_CORRECTION_NACK          0x8000
#define IEEE802154_HEADER_IE_TIME_CORRECTION_MASK          0x0fff
#define IEEE802154_HEADER_IE_TIME_CORRECTION_SIGN_BIT_MASK 0x0800

#define IEEE802154_DEFINE_HEADER_IE_TIME_CORRECTION_CONTENT(_ack, _time_correction_us)             \
	(struct ieee802154_header_ie_time_correction) {                                            \
		.time_sync_info = sys_cpu_to_le16(                                                 \
			(!(_ack) * IEEE802154_HEADER_IE_TIME_CORRECTION_NACK) |                    \
			((_time_correction_us) & IEEE802154_HEADER_IE_TIME_CORRECTION_MASK)),      \
	}
/** INTERNAL_HIDDEN @endcond */

/**
 * @brief Define a vendor specific header IE, see section 7.4.2.3.
 *
 * @details Example usage (all parameters in little endian):
 *
 * @code{.c}
 *   uint8_t vendor_specific_info[] = {...some vendor specific IE content...};
 *   struct ieee802154_header_ie header_ie = IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC(
 *       {0x9b, 0xb8, 0xea}, vendor_specific_info, sizeof(vendor_specific_info));
 * @endcode
 *
 * @param _vendor_oui an initializer for a 3 byte vendor oui array in little
 * endian
 * @param _vendor_specific_info pointer to a variable length uint8_t array with
 * the vendor specific IE content
 * @param _vendor_specific_info_len the length of the vendor specific IE content
 * (in bytes)
 */
#define IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC(_vendor_oui, _vendor_specific_info,            \
						    _vendor_specific_info_len)                     \
	IEEE802154_DEFINE_HEADER_IE(IEEE802154_HEADER_IE_ELEMENT_ID_VENDOR_SPECIFIC_IE,            \
				    IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC_CONTENT_LEN(       \
					    _vendor_specific_info_len),                            \
				    IEEE802154_DEFINE_HEADER_IE_VENDOR_SPECIFIC_CONTENT(           \
					    _vendor_oui, _vendor_specific_info),                   \
				    vendor_specific)

/**
 * @brief Define a reduced CSL IE, see section 7.4.2.3.
 *
 * @details Example usage (all parameters in CPU byte order):
 *
 * @code{.c}
 *   uint16_t csl_phase = ...;
 *   uint16_t csl_period = ...;
 *   struct ieee802154_header_ie header_ie =
 *       IEEE802154_DEFINE_HEADER_IE_CSL_REDUCED(csl_phase, csl_period);
 * @endcode
 *
 * @param _csl_phase CSL phase in CPU byte order
 * @param _csl_period CSL period in CPU byte order
 */
#define IEEE802154_DEFINE_HEADER_IE_CSL_REDUCED(_csl_phase, _csl_period)                           \
	IEEE802154_DEFINE_HEADER_IE(                                                               \
		IEEE802154_HEADER_IE_ELEMENT_ID_CSL_IE,                                            \
		sizeof(struct ieee802154_header_ie_csl_reduced),                                   \
		IEEE802154_DEFINE_HEADER_IE_CSL_REDUCED_CONTENT(_csl_phase, _csl_period),          \
		csl.reduced)

/**
 * @brief Define a full CSL IE, see section 7.4.2.3.
 *
 * @details Example usage (all parameters in CPU byte order):
 *
 * @code{.c}
 *   uint16_t csl_phase = ...;
 *   uint16_t csl_period = ...;
 *   uint16_t csl_rendezvous_time = ...;
 *   struct ieee802154_header_ie header_ie =
 *       IEEE802154_DEFINE_HEADER_IE_CSL_REDUCED(csl_phase, csl_period, csl_rendezvous_time);
 * @endcode
 *
 * @param _csl_phase CSL phase in CPU byte order
 * @param _csl_period CSL period in CPU byte order
 * @param _csl_rendezvous_time CSL rendezvous time in CPU byte order
 */
#define IEEE802154_DEFINE_HEADER_IE_CSL_FULL(_csl_phase, _csl_period, _csl_rendezvous_time)        \
	IEEE802154_DEFINE_HEADER_IE(IEEE802154_HEADER_IE_ELEMENT_ID_CSL_IE,                        \
				    sizeof(struct ieee802154_header_ie_csl_full),                  \
				    IEEE802154_DEFINE_HEADER_IE_CSL_FULL_CONTENT(                  \
					    _csl_phase, _csl_period, _csl_rendezvous_time),        \
				    csl.full)

/**
 * @brief Define a Time Correction IE, see section 7.4.2.7.
 *
 * @details Example usage (parameter in CPU byte order):
 *
 * @code{.c}
 *   uint16_t time_sync_info = ...;
 *   struct ieee802154_header_ie header_ie =
 *       IEEE802154_DEFINE_HEADER_IE_TIME_CORRECTION(true, time_sync_info);
 * @endcode
 *
 * @param _ack whether or not the enhanced ACK frame that receives this IE is an
 * ACK (true) or NACK (false)
 * @param _time_correction_us the positive or negative deviation from expected
 * RX time in microseconds
 */
#define IEEE802154_DEFINE_HEADER_IE_TIME_CORRECTION(_ack, _time_correction_us)                     \
	IEEE802154_DEFINE_HEADER_IE(                                                               \
		IEEE802154_HEADER_IE_ELEMENT_ID_TIME_CORRECTION_IE,                                \
		sizeof(struct ieee802154_header_ie_time_correction),                               \
		IEEE802154_DEFINE_HEADER_IE_TIME_CORRECTION_CONTENT(_ack, _time_correction_us),    \
		time_correction)

/**
 * Retrieve the time correction value in microseconds from a Time Correction IE,
 * see section 7.4.2.7
 *
 * @param[in] ie pointer to the Time Correction IE structure
 *
 * @return The time correction value in microseconds.
 */
static inline int16_t
ieee802154_header_ie_get_time_correction_us(struct ieee802154_header_ie_time_correction *ie)
{
	if (ie->time_sync_info & IEEE802154_HEADER_IE_TIME_CORRECTION_SIGN_BIT_MASK) {
		/* Negative integer */
		return (int16_t)ie->time_sync_info | ~IEEE802154_HEADER_IE_TIME_CORRECTION_MASK;
	}

	/* Positive integer */
	return (int16_t)ie->time_sync_info & IEEE802154_HEADER_IE_TIME_CORRECTION_MASK;
}

/**
 * Set the element ID of a header IE.
 *
 * @param[in] ie pointer to a header IE
 * @param[in] element_id IE element id in CPU byte order
 */
static inline void ieee802154_header_ie_set_element_id(struct ieee802154_header_ie *ie,
						       uint8_t element_id)
{
	ie->element_id_high = element_id >> 1U;
	ie->element_id_low = element_id & 0x01;
}

/**
 * Get the element ID of a header IE.
 *
 * @param[in] ie pointer to a header IE
 *
 * @return header IE element id in CPU byte order
 */
static inline uint8_t ieee802154_header_ie_get_element_id(struct ieee802154_header_ie *ie)
{
	return (ie->element_id_high << 1U) | ie->element_id_low;
}

/**
 * Parsed header IEs
 *
 * Pointers into the frame will be the main means to present
 * parsed IEs.
 *
 * Where different versions of IEs exist, the version is indicated
 * in the flag bitmap.
 */
struct ieee802154_header_ies {
	struct ieee802154_header_ie_csl *csl;
	struct ieee802154_header_ie_rit *rit;
	struct ieee802154_header_ie_rendezvous_time *rendezvous_time;
	struct ieee802154_header_ie_time_correction *time_correction;
	uint32_t payload_ie_present: 1;       /* signals HT1 termination in the header */
	uint32_t csl_with_rendezvous_time: 1; /* The CSL IE includes a rendezvous time. */
	uint32_t _unused: 30;
};

/**
 * Parse Header Information Elements.
 *
 * @param[in] start Pointer to the first byte in a buffer that encodes Header IEs.
 * @param[in] remaining_length Number of (unparsed) bytes in the buffer.
 * @param[out] header_ies Pointer to the structure that will contain the parsed Header IEs.
 *
 * @return 0 on success, a negative value otherwise
 */
int ieee802154_parse_header_ies(uint8_t *start, uint8_t remaining_length,
				struct ieee802154_header_ies *header_ies);

/** The length in bytes of a "Time Correction" header IE. */
#define IEEE802154_TIME_CORRECTION_HEADER_IE_LEN                                                   \
	(IEEE802154_HEADER_IE_HEADER_LENGTH + sizeof(struct ieee802154_header_ie_time_correction))

/**
 * Write a "Time Correction" Header IE to the given buffer.
 *
 * @param[in] frame Pointer to the frame buffer to which the IE is to be
 * written.
 * @param[in] is_nack whether the enhanced ACK frame containing this IE is a NACK
 * @param[in] time_correction_us positive or negative time correction in the
 * range from -2048 microseconds to +2047 microseconds
 */
void ieee802154_write_time_correction_header_ie(struct net_buf *frame, bool is_nack,
						int16_t time_correction_us);

/** The length in bytes of a "Header Termination 1" header IE. */
#define IEEE802154_HEADER_TERMINATION_1_HEADER_IE_LEN IEEE802154_HEADER_IE_HEADER_LENGTH

/**
 * Write a "Header Termination 1" Header IE to the given buffer.
 *
 * @param[in] frame Pointer to the frame buffer to which the IE is to be
 * written.
 */
void ieee802154_write_header_termination_1_header_ie(struct net_buf *frame);

/**
 * @}
 *
 * @}
 */

#endif /* __IEEE802154_IE_H__ */
