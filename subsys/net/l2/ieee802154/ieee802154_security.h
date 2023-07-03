/*
 * Copyright (c) 2017 Intel Corporation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief 802.15.4 6LoWPAN authentication and encryption
 *
 * This is not to be included by the application.
 */

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY

#include <zephyr/net/ieee802154.h>

int ieee802154_security_setup_session(struct ieee802154_security_ctx *sec_ctx, uint8_t level,
				      uint8_t key_mode, uint8_t *key, uint8_t key_len);

void ieee802154_security_teardown_session(struct ieee802154_security_ctx *sec_ctx);

/**
 * @brief Decrypt an authenticated payload.
 *
 * @param ctx Pointer to the IEEE 802.15.4 context.
 * @param frame_type The IEEE 802.15.4 frame type
 * @param frame Pointer to the frame data in original (little endian) byte order.
 * @param ll_hdr_len Length of the MHR.
 * @param payload_len Length of the MAC payload.
 * @param authtag_len Length of the authentication tag.
 * @param src_addr Pointer to the source address of the frame (in little endian byte order).
 * @param src_addr_mode The source address mode (short vs. extended)
 * @param frame_counter_or_asn Frame counter or ASN (in TSCH mode) in CPU byte order.
 */
bool ieee802154_decrypt_auth(struct ieee802154_context *ctx, int frame_type, uint8_t *frame,
			     uint8_t ll_hdr_len, uint8_t payload_len, uint8_t authtag_len,
			     struct ieee802154_address *src_addr, int src_addr_mode,
			     uint64_t frame_counter_or_asn);

/**
 * @brief Encrypt an authenticated payload.
 *
 * @param ctx Pointer to the IEEE 802.15.4 context.
 * @param frame_type The IEEE 802.15.4 frame type
 * @param frame Pointer to the frame data in original (little endian) byte order.
 * @param ll_hdr_len Length of the MHR.
 * @param payload_len Length of the MAC payload.
 * @param authtag_len Length of the authentication tag.
 * @param src_addr Pointer to the source address of the frame (in little endian byte order).
 * @param src_addr_mode The source address mode (short vs. extended)
 * @param frame_counter_or_asn Frame counter or ASN (in TSCH mode) in CPU byte order.
 */
bool ieee802154_encrypt_auth(struct ieee802154_context *ctx, int frame_type, uint8_t *frame,
			     uint8_t ll_hdr_len, uint8_t payload_len, uint8_t authtag_len,
			     struct ieee802154_address *src_addr, int src_addr_mode,
			     uint64_t frame_counter_or_asn);

int ieee802154_security_init(struct ieee802154_security_ctx *sec_ctx);

#else

#define ieee802154_decrypt_auth(...)  true
#define ieee802154_encrypt_auth(...)  true
#define ieee802154_security_init(...) 0

#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */
